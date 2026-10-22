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
import logging

from dynamiq.nodes.agents.exceptions import ToolExecutionException
from dynamiq.nodes.node import ensure_config
from dynamiq.nodes.tools import HttpApiCall
from dynamiq.nodes.tools.http_api_call import HttpApiCallInputSchema
from dynamiq.runnables import RunnableConfig
from dynamiq.types.cancellation import check_cancellation

from app.integrations.safe_http_client import SafeHttpClient, SafeHttpClientError
from app.integrations.write_tool_gate import enforce_write_tool_gate_or_raise
from app.services.egress_allowlist import list_egress_allowlist
from app.services.identity import DelegatedIdentityContext, mint_delegated_token

logger = logging.getLogger(__name__)


class SafeHttpApiCall(HttpApiCall):
    """Same tool/description/input schema as Dynamiq's `HttpApiCall`, except every real
    request goes through `SafeHttpClient` (egress allowlist, internal-IP blocking,
    DNS-rebinding-safe pinned connect, capped redirects/timeout/response size)."""

    company_id: str
    auth_mode: str = "service"
    audience: str | None = None
    identity: DelegatedIdentityContext | None = None
    secret: str | None = None
    # PRD §6.5/§8.1: human-approval gate for `access_level: write` tools. `tool_id`/
    # `run_id` key the `runtime.tool_approvals` row this call creates; `allow_write_tools`
    # is the calling deployment's own flag (always True for Playground/eval, which have no
    # deployment to disallow it). All four are unused (no gate at all) when
    # `access_level == "read"`, the common case.
    tool_id: str = ""
    access_level: str = "read"
    run_id: str | None = None
    allow_write_tools: bool = True
    # The event loop `app.services.compiler.build_agent` was running on when this tool was
    # built (always the server's main loop, real run or not). `list_egress_allowlist` goes
    # through `app.core.db`'s process-wide cached `AsyncEngine`, whose pooled asyncpg
    # connections are bound to whichever loop first opened them - almost always this main
    # loop, since `resolve_and_compile` already runs DB queries on it before `execute()`
    # is ever reached. Routing the coroutine back here with `run_coroutine_threadsafe`
    # (instead of `asyncio.run()`, which would spin up a brand new loop in the worker
    # thread) keeps every DB call on the loop its connections actually belong to -
    # confirmed required by reproducing a real
    # `asyncpg.exceptions.InternalClientError: got result for unknown protocol state`
    # otherwise.
    loop: asyncio.AbstractEventLoop | None = None

    def _safe_request_kwargs(self, input_data: HttpApiCallInputSchema) -> dict:
        request_kwargs = self._build_request_kwargs(input_data)
        # SafeHttpClient enforces its own fixed timeout as part of the "must go through
        # this client" contract - the node's own configurable `timeout` field (passed by
        # the parent's _build_request_kwargs) doesn't apply when routed through it.
        request_kwargs.pop("timeout", None)
        if self.auth_mode == "delegated":
            if self.identity is None:
                # Fail closed, not fail open: a missing identity must never fall back to
                # minting a blank-sub/act/run_id-but-still-validly-signed token - the
                # entire point of PRD §7.5 is that a valid signature implies a real,
                # attributable caller.
                raise ToolExecutionException(
                    f"Tool {self.name!r} is auth_mode='delegated' but no run identity was supplied - refusing to "
                    "mint an anonymous delegated token.",
                    recoverable=False,
                )
            identity = self.identity
            token = mint_delegated_token(
                self.company_id, identity.sub, identity.claims, identity.act, identity.run_id, self.audience or ""
            )
            # Overwritten last, after `_build_request_kwargs` already merged the LLM's own
            # tool-call input headers in - a prompt-injected `Authorization` header must
            # never be able to suppress or replace the real delegated identity.
            request_kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
            logger.info(
                "delegated identity token attached: tool=%s run_id=%s act=%s sub=%s",
                self.name,
                identity.run_id,
                identity.act,
                identity.sub,
            )
        elif self.auth_mode == "service" and self.secret is not None:
            # Fail open, not fail closed: unlike a missing delegated identity, a
            # service-mode tool with no secret configured is a normal, pre-existing
            # state (plenty of real tools need no credential at all) - nothing to attach,
            # no error. Same anti-prompt-injection ordering as the delegated branch.
            request_kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {self.secret}"
        return request_kwargs

    async def _run_safe_request(self, request_kwargs: dict):
        allowlist = await list_egress_allowlist(self.company_id)
        return await SafeHttpClient(allowlist).request(**request_kwargs)

    def _run_safe_request_from_thread(self, request_kwargs: dict):
        """Dispatches `_run_safe_request` from the worker thread `execute()` runs in back
        onto `self.loop` and blocks until it's done - see the `loop` field's docstring for
        why `asyncio.run()` alone is unsafe here. Falls back to `asyncio.run()` when no
        loop was supplied (never the real run-time case - see `loop`'s docstring - but
        keeps this usable standalone, e.g. in a unit test that builds the tool directly)."""
        if self.loop is not None:
            return asyncio.run_coroutine_threadsafe(self._run_safe_request(request_kwargs), self.loop).result()
        return asyncio.run(self._run_safe_request(request_kwargs))

    async def _enforce_write_gate(self, input_data: HttpApiCallInputSchema) -> None:
        """The human-approval gate (PRD §6.5/§8.1) for `access_level == "write"` - called
        from `execute`/`execute_async` *before* `_safe_request_kwargs` builds the real
        outbound request, never after: that's the step that attaches a `service`-mode
        secret or mints a `delegated` JWT into `request_kwargs["headers"]`, and the
        `request` this stores into `runtime.tool_approvals` (readable by the company's own
        console users) must never carry either. `input_data.model_dump(mode="json")` is
        exactly what the LLM chose to call this tool with - nothing more."""
        await enforce_write_tool_gate_or_raise(
            self.name,
            self.company_id,
            self.run_id,
            self.tool_id,
            self.allow_write_tools,
            request={"tool_name": self.name, "input": input_data.model_dump(mode="json")},
        )

    def _enforce_write_gate_from_thread(self, input_data: HttpApiCallInputSchema) -> None:
        """Sync counterpart of `_enforce_write_gate`, same dispatch pattern as
        `_run_safe_request_from_thread` - see that method's docstring."""
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._enforce_write_gate(input_data), self.loop).result()
        else:
            asyncio.run(self._enforce_write_gate(input_data))

    def execute(self, input_data: HttpApiCallInputSchema, config: RunnableConfig = None, **kwargs):
        config = ensure_config(config)
        check_cancellation(config)
        self.run_on_node_execute_run(config.callbacks, **kwargs)

        if self.access_level == "write":
            self._enforce_write_gate_from_thread(input_data)

        request_kwargs = self._safe_request_kwargs(input_data)
        try:
            # Called from a plain worker thread (`asyncio.to_thread(agent.run, ...)` in
            # app.services.runtime), never the main event loop - see `_run_safe_request_from_thread`.
            response = self._run_safe_request_from_thread(request_kwargs)
        except SafeHttpClientError as exc:
            raise ToolExecutionException(
                f"Request blocked: {exc}. Please analyze the error and take appropriate action.",
                recoverable=True,
            ) from exc
        return self._parse_response(response)

    async def execute_async(self, input_data: HttpApiCallInputSchema, config: RunnableConfig = None, **kwargs):
        config = ensure_config(config)
        self.run_on_node_execute_run(config.callbacks, **kwargs)

        if self.access_level == "write":
            await self._enforce_write_gate(input_data)

        request_kwargs = self._safe_request_kwargs(input_data)
        try:
            response = await self._run_safe_request(request_kwargs)
        except SafeHttpClientError as exc:
            raise ToolExecutionException(
                f"Request blocked: {exc}. Please analyze the error and take appropriate action.",
                recoverable=True,
            ) from exc
        return self._parse_response(response)
