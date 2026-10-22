"""Shared human-approval gate translation for `access_level: write` tools (PRD §6.5/§8.1).

`SafeHttpApiCall` (`safe_http_tool.py`) and `_SafeBuiltinSearchTool` (`safe_builtin_tools.py`)
both need the exact same thing here: call `app.services.approvals.enforce_write_tool_gate`,
push the PRD's `approval_required` SSE event the moment a `pending` row exists, and turn its
three outcome exceptions into one consistent, recoverable `ToolExecutionException` message -
`app.services.approvals` itself stays dynamiq-agnostic (same reasoning it already documents
for not importing dynamiq at all), so this one function is the single place that bridges the
two.
"""
from dynamiq.callbacks.streaming import AsyncStreamingIteratorCallbackHandler
from dynamiq.nodes.agents.exceptions import ToolExecutionException
from dynamiq.types.streaming import StreamingEventMessage

from app.services.approvals import (
    ApprovalExpiredError,
    ApprovalRejectedError,
    WriteToolsNotAllowedError,
    enforce_write_tool_gate,
)


async def enforce_write_tool_gate_or_raise(
    tool_name: str,
    company_id: str,
    run_id: str | None,
    tool_id: str,
    allow_write_tools: bool,
    request: dict,
    stream_handler: AsyncStreamingIteratorCallbackHandler | None = None,
) -> None:
    """Returns normally only when a human approved the call. `request` must already be
    built from the LLM's own tool-call input alone (e.g. `input_data.model_dump(mode=
    "json")`) - never from a fully-built outbound request, which could carry a
    `service`-mode secret or a `delegated` JWT in its headers that a company's own console
    users (who can read `runtime.tool_approvals`) must never see. Callers are responsible
    for calling this *before* any such secret/JWT is attached.

    `stream_handler` is the current run's live-streaming handle (PRD Goal #6) - `None`
    whenever the run isn't being streamed at all. When given, `_notify_pending` below fires
    PRD §6.5's `approval_required` SSE event the instant the `pending` row exists, via
    `send_to_queue` - dynamiq's own thread-safe queue-write method (it internally
    dispatches through `asyncio.run_coroutine_threadsafe` onto the handler's owning loop),
    the same mechanism dynamiq's own `AgentStreamingParserCallback` uses to get live
    tokens out of the `agent.run()` worker thread - never call `stream_handler.queue.
    put_nowait(...)` directly from here, which isn't thread-safe cross-thread.
    `app.services.streaming_events.extract_approval_required_event` is the paired
    unwrap on the SSE-emitting side (`main_gateway.py`/`main_runtime.py`). The
    `dynamiq_console_event` marker key keeps this unambiguous from every real dynamiq
    chunk, which is always the `{"choices": [{"delta": {...}}]}` wrapper
    `extract_answer_delta`/`extract_tool_step_event` parse instead."""

    def _notify_pending(approval_id: str) -> None:
        if stream_handler is None:
            return
        stream_handler.send_to_queue(
            StreamingEventMessage(
                data={
                    "dynamiq_console_event": "approval_required",
                    "approval_id": approval_id,
                    "tool": tool_name,
                }
            )
        )

    try:
        await enforce_write_tool_gate(
            company_id, run_id or "", tool_id, allow_write_tools, request=request, on_pending=_notify_pending
        )
    except WriteToolsNotAllowedError as exc:
        raise ToolExecutionException(
            f"Tool {tool_name!r} requires write access, which this deployment does not allow.",
            recoverable=True,
        ) from exc
    except ApprovalRejectedError as exc:
        raise ToolExecutionException(
            f"Tool {tool_name!r} call was rejected by a human reviewer.", recoverable=True
        ) from exc
    except ApprovalExpiredError as exc:
        raise ToolExecutionException(
            f"Tool {tool_name!r} call timed out waiting for human approval.", recoverable=True
        ) from exc
