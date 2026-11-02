"""Approval-gated E2B sandbox code execution (PRD §6.1/§6.6a, §6.5/§8.1).

Dynamiq's own `E2BInterpreterTool` just executes whatever Python/shell the LLM wrote, no
approval step - unlike `SafeHttpApiCall`/`SafeTavilyTool`/`SafeExaTool`, where the human-
approval gate only fires for `access_level: "write"` tools (the common case is a plain
read, e.g. a GET call or a web search). Arbitrary agent-written code execution has no such
"obviously harmless" common case - even a read-only-looking snippet can exfiltrate data,
burn sandbox quota, or loop forever - so this subclass always runs the gate, regardless of
how the tool's own `access_level` is configured (admin-only to attach at all, per
`app.services.node_allowlist.ADMIN_ONLY_NODE_TYPES`, but still subject to the same
per-call human decision every other write-tool call needs).
"""
import asyncio

from dynamiq.callbacks.streaming import AsyncStreamingIteratorCallbackHandler
from dynamiq.nodes.node import ensure_config
from dynamiq.nodes.tools.code_interpreter import CodeInterpreterInputSchema
from dynamiq.nodes.tools.e2b_sandbox import E2BInterpreterTool
from dynamiq.runnables import RunnableConfig
from dynamiq.types.cancellation import check_cancellation

from app.integrations.write_tool_gate import enforce_write_tool_gate_or_raise


class SafePythonTool(E2BInterpreterTool):
    """Same tool/description/input schema as Dynamiq's `E2BInterpreterTool`, except every
    call is gated on human approval first - see this module's own docstring for why that's
    unconditional here, unlike every other `Safe*` tool wrapper in this package."""

    company_id: str
    tool_id: str = ""
    access_level: str = "read"
    run_id: str | None = None
    allow_write_tools: bool = True
    stream_handler: AsyncStreamingIteratorCallbackHandler | None = None
    # See SafeHttpApiCall.loop's docstring - same reasoning applies to this tool's own
    # write-gate DB call (a plain worker-thread `execute()`, never the main event loop).
    loop: asyncio.AbstractEventLoop | None = None

    async def _enforce_write_gate(self, input_data: CodeInterpreterInputSchema) -> None:
        await enforce_write_tool_gate_or_raise(
            self.name,
            self.company_id,
            self.run_id,
            self.tool_id,
            self.allow_write_tools,
            request={"tool_name": self.name, "input": input_data.model_dump(mode="json")},
            stream_handler=self.stream_handler,
        )

    def _enforce_write_gate_from_thread(self, input_data: CodeInterpreterInputSchema) -> None:
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._enforce_write_gate(input_data), self.loop).result()
        else:
            asyncio.run(self._enforce_write_gate(input_data))

    def execute(self, input_data: CodeInterpreterInputSchema, config: RunnableConfig = None, **kwargs):
        # No `run_on_node_execute_run` call here, unlike `SafeHttpApiCall` - the parent's
        # own `execute()` already fires it itself (with real `tool_data` carrying the
        # sandbox session id/host, once the sandbox actually exists), so calling it again
        # here would double-fire the callback `_StepCollector` listens to for trace steps.
        config = ensure_config(config)
        check_cancellation(config)

        self._enforce_write_gate_from_thread(input_data)

        return super().execute(input_data, config=config, **kwargs)
