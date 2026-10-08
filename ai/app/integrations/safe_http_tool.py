"""Egress-safe `HttpApiCall` tool (PRD §7.4).

Dynamiq's own `HttpApiCall.execute`/`execute_async` send requests through a bare
`requests`/`httpx.AsyncClient` built from its `connection` - no SSRF/egress-allowlist
check at all, and the LLM can override the configured `url` entirely at call time via
`HttpApiCallInputSchema.url` (`_build_request_kwargs`: `input_data.url or self.url or
self.connection.url`). `app.integrations.safe_http_client`'s own module docstring says
every outbound HTTP call - HTTP Tools included - must go through `SafeHttpClient`
instead, so this subclass routes every real request there and re-checks the egress
allowlist on every call (not just once when the agent was compiled/attached) so a
company's allowlist change takes effect on the agent's very next tool call, not only
after a recompile.
"""
import asyncio

from dynamiq.nodes.agents.exceptions import ToolExecutionException
from dynamiq.nodes.node import ensure_config
from dynamiq.nodes.tools import HttpApiCall
from dynamiq.nodes.tools.http_api_call import HttpApiCallInputSchema
from dynamiq.runnables import RunnableConfig
from dynamiq.types.cancellation import check_cancellation

from app.integrations.safe_http_client import SafeHttpClient, SafeHttpClientError
from app.services.egress_allowlist import list_egress_allowlist


class SafeHttpApiCall(HttpApiCall):
    """Same tool/description/input schema as Dynamiq's `HttpApiCall`, except every real
    request goes through `SafeHttpClient` (egress allowlist, internal-IP blocking,
    DNS-rebinding-safe pinned connect, capped redirects/timeout/response size)."""

    company_id: str

    def _safe_request_kwargs(self, input_data: HttpApiCallInputSchema) -> dict:
        request_kwargs = self._build_request_kwargs(input_data)
        # SafeHttpClient enforces its own fixed timeout as part of the "must go through
        # this client" contract - the node's own configurable `timeout` field (passed by
        # the parent's _build_request_kwargs) doesn't apply when routed through it.
        request_kwargs.pop("timeout", None)
        return request_kwargs

    async def _run_safe_request(self, request_kwargs: dict):
        allowlist = await list_egress_allowlist(self.company_id)
        return await SafeHttpClient(allowlist).request(**request_kwargs)

    def execute(self, input_data: HttpApiCallInputSchema, config: RunnableConfig = None, **kwargs):
        config = ensure_config(config)
        check_cancellation(config)
        self.run_on_node_execute_run(config.callbacks, **kwargs)

        request_kwargs = self._safe_request_kwargs(input_data)
        try:
            # Called from a plain worker thread (`asyncio.to_thread(agent.run, ...)` in
            # app.services.runtime), never the main event loop - asyncio.run() is safe here.
            response = asyncio.run(self._run_safe_request(request_kwargs))
        except SafeHttpClientError as exc:
            raise ToolExecutionException(
                f"Request blocked: {exc}. Please analyze the error and take appropriate action.",
                recoverable=True,
            ) from exc
        return self._parse_response(response)

    async def execute_async(self, input_data: HttpApiCallInputSchema, config: RunnableConfig = None, **kwargs):
        config = ensure_config(config)
        self.run_on_node_execute_run(config.callbacks, **kwargs)

        request_kwargs = self._safe_request_kwargs(input_data)
        try:
            response = await self._run_safe_request(request_kwargs)
        except SafeHttpClientError as exc:
            raise ToolExecutionException(
                f"Request blocked: {exc}. Please analyze the error and take appropriate action.",
                recoverable=True,
            ) from exc
        return self._parse_response(response)
