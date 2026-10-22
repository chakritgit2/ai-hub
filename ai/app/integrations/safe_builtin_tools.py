"""Egress-safe Tavily/Exa web-search tools (PRD §6.5, §7.4).

Dynamiq's own `TavilyTool`/`ExaTool` send every real request through their own
`self.client`/`self.get_async_client()` (built straight from the `Tavily`/`Exa`
connection) - no `SafeHttpClient`/egress-allowlist check at all, the same gap
`app.integrations.safe_http_tool.SafeHttpApiCall` already closes for `kind: http` tools.
Tavily/Exa's own API host is fixed (not a user-supplied URL the way an HTTP tool's is, so
not a classic SSRF vector), but PRD §6.5 states "all outbound calls go through
SafeHttpClient" without carving out an exception for built-in tools, so these subclasses
route every real request there too, for the same single-audited-egress-path reasoning.

Each subclass reuses its parent's own private request-building/response-formatting
helpers (`_build_search_payload`, `_format_tavily_result`/`_format_search_response`,
`_wrap_search_exception`) and only replaces how the request is actually sent.
"""
import asyncio

from dynamiq.nodes.node import ensure_config
from dynamiq.nodes.tools.exa_search import ExaInputSchema, ExaTool
from dynamiq.nodes.tools.tavily import TavilyInputSchema, TavilyTool
from dynamiq.runnables import RunnableConfig
from dynamiq.types.cancellation import check_cancellation

from app.integrations.safe_http_client import SafeHttpClient
from app.integrations.write_tool_gate import enforce_write_tool_gate_or_raise
from app.services.egress_allowlist import list_egress_allowlist


class _SafeBuiltinSearchTool:
    """Shared by `SafeTavilyTool`/`SafeExaTool` - not a `ConnectionNode` itself (that base
    class differs per provider), just the behavior both need identically: sending a
    request through `SafeHttpClient` instead of the parent's own
    `self.client`/`self.get_async_client()`, and the `access_level: write` human-approval
    gate (PRD §6.5/§8.1 - unusual for a search tool, but `console.tools.access_level`
    isn't restricted by kind)."""

    company_id: str
    tool_id: str = ""
    access_level: str = "read"
    run_id: str | None = None
    allow_write_tools: bool = True
    # See `app.integrations.safe_http_tool.SafeHttpApiCall.loop`'s docstring - same
    # cross-loop asyncpg hazard, same fix: route the egress-allowlist DB call back onto
    # the loop its pooled connections actually belong to instead of spinning up a new one.
    loop: asyncio.AbstractEventLoop | None = None

    async def _enforce_write_gate(self, payload: dict) -> None:
        """Same gate as `SafeHttpApiCall._enforce_write_gate` - see that docstring for why
        this must run before any real request is built. `payload` here is already just
        the search query/params the LLM supplied (`_build_search_payload`'s own output),
        never anything with a credential attached."""
        await enforce_write_tool_gate_or_raise(
            self.name,
            self.company_id,
            self.run_id,
            self.tool_id,
            self.allow_write_tools,
            request={"tool_name": self.name, "input": payload},
        )

    def _enforce_write_gate_from_thread(self, payload: dict) -> None:
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._enforce_write_gate(payload), self.loop).result()
        else:
            asyncio.run(self._enforce_write_gate(payload))

    async def _run_safe_request(self, method: str, url: str, json: dict, headers: dict | None = None):
        allowlist = await list_egress_allowlist(self.company_id)
        return await SafeHttpClient(allowlist).request(method=method, url=url, json=json, headers=headers or {})

    def _run_safe_request_from_thread(self, method: str, url: str, json: dict, headers: dict | None = None):
        if self.loop is not None:
            return asyncio.run_coroutine_threadsafe(
                self._run_safe_request(method, url, json, headers), self.loop
            ).result()
        return asyncio.run(self._run_safe_request(method, url, json, headers))


class SafeTavilyTool(_SafeBuiltinSearchTool, TavilyTool):
    def execute(self, input_data: TavilyInputSchema, config: RunnableConfig | None = None, **kwargs):
        config = ensure_config(config)
        check_cancellation(config)
        self.run_on_node_execute_run(config.callbacks, **kwargs)

        if self.access_level == "write":
            self._enforce_write_gate_from_thread(input_data.model_dump(mode="json"))

        request_payload, connection_url = self._build_search_payload(input_data)
        try:
            # Called from a plain worker thread (`asyncio.to_thread(agent.run, ...)` in
            # app.services.runtime), never the main event loop - see
            # `_run_safe_request_from_thread`.
            response = self._run_safe_request_from_thread(
                self.connection.method, connection_url, {**self.connection.data, **request_payload}
            )
            response.raise_for_status()
            search_result = response.json()
        except Exception as exc:
            raise self._wrap_search_exception(exc)

        return self._format_tavily_result(search_result, request_payload, input_data)

    async def execute_async(self, input_data: TavilyInputSchema, config: RunnableConfig | None = None, **kwargs):
        config = ensure_config(config)
        check_cancellation(config)
        self.run_on_node_execute_run(config.callbacks, **kwargs)

        if self.access_level == "write":
            await self._enforce_write_gate(input_data.model_dump(mode="json"))

        request_payload, connection_url = self._build_search_payload(input_data)
        try:
            response = await self._run_safe_request(
                self.connection.method, connection_url, {**self.connection.data, **request_payload}
            )
            response.raise_for_status()
            search_result = response.json()
        except Exception as exc:
            raise self._wrap_search_exception(exc)

        return self._format_tavily_result(search_result, request_payload, input_data)


class SafeExaTool(_SafeBuiltinSearchTool, ExaTool):
    def execute(self, input_data: ExaInputSchema, config: RunnableConfig | None = None, **kwargs):
        config = ensure_config(config)
        check_cancellation(config)
        self.run_on_node_execute_run(config.callbacks, **kwargs)

        if self.access_level == "write":
            self._enforce_write_gate_from_thread(input_data.model_dump(mode="json"))

        payload, connection_url = self._build_search_payload(input_data)
        try:
            response = self._run_safe_request_from_thread(
                self.connection.method, connection_url, payload, self.connection.headers
            )
            response.raise_for_status()
            search_result = response.json()
        except Exception as exc:
            raise self._wrap_search_exception(exc)

        return self._format_search_response(search_result)

    async def execute_async(self, input_data: ExaInputSchema, config: RunnableConfig | None = None, **kwargs):
        config = ensure_config(config)
        check_cancellation(config)
        self.run_on_node_execute_run(config.callbacks, **kwargs)

        if self.access_level == "write":
            await self._enforce_write_gate(input_data.model_dump(mode="json"))

        payload, connection_url = self._build_search_payload(input_data)
        try:
            response = await self._run_safe_request(
                self.connection.method, connection_url, payload, self.connection.headers
            )
            response.raise_for_status()
            search_result = response.json()
        except Exception as exc:
            raise self._wrap_search_exception(exc)

        return self._format_search_response(search_result)
