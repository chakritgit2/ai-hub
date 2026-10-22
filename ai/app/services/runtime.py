"""Agent run execution - the one fully real, load-bearing piece of this skeleton. Both
entry points resolve a company's own agent version, compile it live, and run a real
Dynamiq `Agent` built from a real decrypted connection secret:

- `run_playground_agent` - PRD §4.4-B, a verified runtime token's `agent_version_id`/
  `role`, no quota (Playground is never counted against a deployment's daily budget).
- `run_deployment_agent` - PRD §4.4-C/§6.3, a gateway API key/session token's resolved
  deployment, with the deployment's own `guardrail_overrides` layered on top of the
  agent version's guardrails and real quota reservation/reconciliation
  (`app.services.quotas`) wrapped around the run.

Both share the same core (`execute_agent_run`): guardrail checks, memory (PRD §6.2),
usage tracking, and `logs.runs`/`logs.guardrail_events` writes. `execute_agent_run` is
public (no leading underscore) since `app.services.eval_runner` also calls it directly,
once per dataset item, same reasoning `resolve_and_compile` below already documents for
itself.
"""
import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from dynamiq.callbacks.base import BaseCallbackHandler, get_run_id
from dynamiq.callbacks.streaming import AsyncStreamingIteratorCallbackHandler
from dynamiq.runnables.base import RunnableConfig, RunnableStatus
from dynamiq.types.streaming import StreamingConfig, StreamingEventMessage, StreamingMode

from app.core.config import get_settings
from app.integrations.skill_registry import ConsoleSkillRegistry, get_console_skill_registry
from app.services.agent_spec import GuardrailsSpec, MemorySpec
from app.services.agent_versions import resolve_agent_version
from app.services.company_budget import check_monthly_budget_not_exceeded
from app.services.compiler import BUILTIN_TOOL_TYPES, RETRIEVAL_TOOL_TYPE, build_agent, compile_spec
from app.services.connection_secrets import decrypt_connection_secret
from app.services.conversations import get_or_create_conversation
from app.services.deployments import resolve_deployment
from app.services.egress_allowlist import list_egress_allowlist
from app.services.guardrails import log_guardrail_events, run_checks
from app.services.identity import DelegatedIdentityContext
from app.services.quotas import QuotaExceededError, check_rate_limit, reconcile_quota, reserve_quota
from app.services.run_steps import RunStep, guardrail_run_steps, log_run_steps
from app.services.runs import RunUsage, log_run
from app.services.tool_secrets import decrypt_tool_secret


class _UsageCollector(BaseCallbackHandler):
    """Collects the `usage_data` dict dynamiq's LLM nodes report to
    `on_node_execute_run` on every completion call (dynamiq/nodes/llms/base.py) -
    `Agent.run()`'s own return value carries no token/cost info at all, this is the only
    place it's available. Threaded through via `RunnableConfig(callbacks=[...])`, which
    `Agent.run()` passes down unchanged into its own `self.llm.run(config=config, ...)`
    call - verified against the installed dynamiq==0.65.0 source.
    """

    def __init__(self) -> None:
        super().__init__()
        self.usage: list[dict[str, Any]] = []

    def on_node_execute_run(self, serialized: dict[str, Any], **kwargs: Any) -> None:
        usage_data = kwargs.get("usage_data")
        if usage_data:
            self.usage.append(usage_data)

    def total_usage(self) -> RunUsage:
        return RunUsage(
            tokens_in=sum(u.get("prompt_tokens") or 0 for u in self.usage) or None,
            tokens_out=sum(u.get("completion_tokens") or 0 for u in self.usage) or None,
            cost_usd=sum(u.get("total_tokens_cost_usd") or 0 for u in self.usage) or None,
        )


def _step_text(value: Any) -> str | None:
    """Best-effort stringification of a node's input/output dict for `logs.run_steps`
    (always a dict/BaseException from dynamiq, never already a plain string)."""
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except TypeError:
        return str(value)


class _StepCollector(BaseCallbackHandler):
    """Collects one `RunStep` per LLM/tool node execution, in call order, for
    `logs.run_steps` (PRD §6.8/§6.9 Trace tab). `on_node_start`/`on_node_end`/
    `on_node_error` are the only hooks dynamiq fires identically for every node type -
    LLM or Tool - verified against the installed dynamiq==0.65.0 source
    (dynamiq/nodes/node.py's run_sync/_run_async_native call these for every `Node`
    subclass, including tool nodes run via `Agent._run_tool`). `on_node_execute_run` is
    read opportunistically to enrich LLM steps with `usage_data` the same way
    `_UsageCollector` does, since only LLM nodes attach it.

    The top-level Agent node itself (`serialized["group"] == "agents"`) is skipped - its
    input/output/timing already duplicate `logs.runs`. Since `agent.run()` executes
    synchronously in a single thread (the ReAct loop calls one node at a time), node
    `on_node_end`/`on_node_error` calls arrive in true execution order, so `self.steps`
    needs no re-sorting.
    """

    _STEP_GROUPS = {"llms": "llm", "tools": "tool_call"}

    def __init__(self) -> None:
        super().__init__()
        self.steps: list[RunStep] = []
        self._pending: dict[Any, dict[str, Any]] = {}

    def on_node_start(self, serialized: dict[str, Any], input_data: dict[str, Any], **kwargs: Any) -> None:
        step_type = self._STEP_GROUPS.get(serialized.get("group"))
        if step_type is None:
            return
        self._pending[get_run_id(kwargs)] = {
            "step_type": step_type,
            "name": serialized.get("name") or serialized.get("type"),
            "input": _step_text(input_data),
            "started": time.monotonic(),
            "usage": None,
        }

    def on_node_execute_run(self, serialized: dict[str, Any], **kwargs: Any) -> None:
        if serialized.get("group") != "llms":
            return
        pending = self._pending.get(get_run_id(kwargs))
        usage_data = kwargs.get("usage_data")
        if pending is not None and usage_data:
            pending["usage"] = usage_data

    def on_node_end(self, serialized: dict[str, Any], output_data: dict[str, Any], **kwargs: Any) -> None:
        if serialized.get("group") not in self._STEP_GROUPS:
            return
        self._finish(get_run_id(kwargs), output=_step_text(output_data), error=None, status="success")

    def on_node_error(self, serialized: dict[str, Any], error: BaseException, **kwargs: Any) -> None:
        if serialized.get("group") not in self._STEP_GROUPS:
            return
        self._finish(get_run_id(kwargs), output=None, error=str(error), status="error")

    def _finish(self, run_id: Any, *, output: str | None, error: str | None, status: str) -> None:
        pending = self._pending.pop(run_id, None)
        if pending is None:
            return
        usage = pending["usage"] or {}
        self.steps.append(
            RunStep(
                step_type=pending["step_type"],
                name=pending["name"],
                status=status,
                input_text=pending["input"],
                output_text=output,
                tokens_in=usage.get("prompt_tokens"),
                tokens_out=usage.get("completion_tokens"),
                cost_usd=usage.get("total_tokens_cost_usd"),
                latency_ms=int((time.monotonic() - pending["started"]) * 1000),
                error=error,
            )
        )


def _effective_run_timeout(agent_def: dict, settings) -> int:
    """`Settings.RUN_TIMEOUT_SECONDS`, extended by one `Settings.APPROVAL_TIMEOUT_SECONDS`
    window when `agent_def` has at least one `access_level: write` tool attached - a
    write-tool call can pause the whole synchronous `agent.run()` for up to that long
    waiting on a human decision (`SafeHttpApiCall._enforce_write_gate`, PRD §6.5), and the
    plain `RUN_TIMEOUT_SECONDS` alone would cut that wait short well before the full
    window. A run with no write tool attached gets exactly `RUN_TIMEOUT_SECONDS`,
    unchanged from before this feature existed. Pulled out of `_execute_agent_run_body` as
    its own pure function purely so it's unit-testable without mocking `asyncio.wait_for`.

    Known limitation: only one approval window is budgeted, regardless of how many
    write-tool calls (same tool retried, or several different ones) actually happen
    during the run - a second approval wait in the same run can still be cut off by this
    timeout if the first one already used up most of it. Rejected a per-call-count budget
    (`max_loops * APPROVAL_TIMEOUT_SECONDS`) as the fix here: with the default `max_loops`
    of 8 that's up to 40 extra minutes a stuck run could tie up a worker thread for, to
    cover a rare compound case - not a trade worth making in this slice.
    """
    has_write_tool = any(tool_def.get("access_level") == "write" for tool_def in agent_def.get("tools", []))
    return settings.RUN_TIMEOUT_SECONDS + (settings.APPROVAL_TIMEOUT_SECONDS if has_write_tool else 0)


async def resolve_and_compile(company_id: str, agent_version_id: str, role: str) -> dict:
    """Resolves `agent_version_id` within `company_id` and compiles its *current* spec
    live (PRD §4.4-B) - never the `compiled_definition` a Publish may have snapshotted,
    since neither a runtime token's agent_version_id nor a deployment's is required to
    be published (RuntimeTokenController::issueRuntimeToken only checks it exists in the
    caller's company). Raises ValueError (not found / compile failure) - callers let it
    bubble up to the existing "genuine failure -> 502" handling in app.main_runtime/
    app.main_gateway, no new error shape needed. Public (no leading underscore) since
    `app.services.eval_runner` also needs this exact "compile the current agent version
    live" step.
    """
    version = await resolve_agent_version(company_id, agent_version_id)
    if version is None:
        raise ValueError(f"agent_version_id {agent_version_id!r} not found for this company")

    result = await compile_spec(version.spec, role, company_id)
    if not result["ok"]:
        raise ValueError(f"agent version {agent_version_id!r} failed to compile: {result['errors']}")

    return result["compiled_definition"]


def _merge_guardrail_overrides(compiled_definition: dict, guardrail_overrides: dict) -> GuardrailsSpec:
    """A deployment can only *tighten* an agent version's guardrails (PRD §6.4: "Policies
    are defined per agent version and can be tightened per deployment"), never loosen
    them - so overrides are appended to the agent version's own checks, never replace
    them. Playground has no deployment, so it always compiles with the agent version's
    guardrails exactly as published/drafted."""
    base = compiled_definition["guardrails"]
    merged = {
        "input": [*base.get("input", []), *guardrail_overrides.get("input", [])],
        "output": [*base.get("output", []), *guardrail_overrides.get("output", [])],
    }
    return GuardrailsSpec.model_validate(merged)


async def execute_agent_run(
    compiled_definition: dict,
    guardrails: GuardrailsSpec,
    *,
    company_id: str,
    agent_version_id: str,
    source: str,
    input_text: str,
    external_user_id: str | None,
    conversation_id: str | None,
    deployment_id: str | None,
    identity_claims: dict | None = None,
    identity_sub: str | None = None,
    run_id: str | None = None,
    stream_handler: AsyncStreamingIteratorCallbackHandler | None = None,
    stream_mode: StreamingMode = StreamingMode.FINAL,
    allow_write_tools: bool = True,
) -> dict:
    """Shared core: guardrail checks, memory, build+run the real Agent, usage tracking,
    `logs.runs`/`logs.guardrail_events` writes. Raises on resolution-adjacent failures
    (missing secret) and on the underlying Dynamiq/OpenAI call itself - callers are
    responsible for turning that into an HTTP error response. A `logs.runs` row with
    status="error" is still written before re-raising, for every failure past the point
    a run_id exists.

    `run_id` lets a caller (`app.services.eval_runner`) generate and keep the id *before*
    calling this, so it still has something to key off of even when this raises before
    ever returning - the return dict itself carries no `run_id` (only `trace_id`, a
    separate externally-facing correlation id already returned to every Playground/API
    caller) to avoid widening that public response shape. Defaults to a fresh uuid4 for
    every existing caller, unchanged from before this parameter existed.

    `stream_handler` (PRD Goal #6) is only actually wired into the run when
    `guardrails.output` is empty - output guardrails can only run *after* `agent.run()`
    returns the complete text (see below), so live token-by-token delivery would let
    unguarded text reach the caller before a block/redact decision is made. When an
    output guardrail is configured, `stream_handler` is accepted but never attached to
    the run; the caller (`app.main_runtime.runPlaygroundStream`) is expected to send the
    already-guardrail-checked `output` as a single chunk instead. The whole function body
    runs inside a `try/finally` purely to guarantee `stream_handler.done_event` is always
    set on every exit path (blocked-input return, exception, success) - a bare
    `Agent.run()` (not wrapped in a `dynamiq.Workflow`) never fires the
    `on_workflow_*` hooks that would otherwise set it, so without this a caller
    iterating `async for event in stream_handler` would hang forever once the real
    answer is done. See `streamed_live` in the returned dict.

    `stream_mode` (PRD Goal #6 follow-up: tool-call status events) controls how much
    dynamiq actually emits onto `stream_handler` when `streamed_live` ends up true -
    `StreamingMode.FINAL` (the default, what every existing caller gets unchanged) only
    ever emits `step="answer"` chunks; `StreamingMode.ALL` additionally unlocks
    `step="reasoning"` (a structured event right before a tool call, carrying the real
    tool name/input) and `step="tool"` (the tool's result, once it finishes) - see
    `app.main_gateway.streamDeployment`'s own docstring for how it turns those into
    `step` SSE events. Has no effect at all when `streamed_live` is false.

    `allow_write_tools` (PRD §6.5/§8.1) gates every `access_level: write` tool this run's
    agent has attached - `False` rejects a write-tool call outright (no human-approval row
    is ever created), `True` (the default - every caller except `run_deployment_agent`)
    lets it through to the approval gate itself. `run_deployment_agent` passes its own
    deployment's `allow_write_tools` flag; Playground/eval have no deployment to disallow
    it, so they always reach the approval gate.

    Returns `{"output": str, "trace_id": str, "latency_ms": int,
    "conversation_id": str | None, "guardrail_events": list, "tokens_in": int | None,
    "tokens_out": int | None, "cost_usd": float | None, "streamed_live": bool}`.
    """
    run_id = run_id or str(uuid4())
    trace_id = str(uuid4())
    started = time.monotonic()
    try:
        return await _execute_agent_run_body(
            compiled_definition,
            guardrails,
            company_id=company_id,
            agent_version_id=agent_version_id,
            source=source,
            input_text=input_text,
            external_user_id=external_user_id,
            conversation_id=conversation_id,
            deployment_id=deployment_id,
            identity_claims=identity_claims,
            identity_sub=identity_sub,
            run_id=run_id,
            trace_id=trace_id,
            started=started,
            stream_handler=stream_handler,
            stream_mode=stream_mode,
            allow_write_tools=allow_write_tools,
        )
    finally:
        if stream_handler is not None:
            stream_handler.done_event.set()


def attach_stream_completion(
    run_task: "asyncio.Task[dict]", stream_handler: AsyncStreamingIteratorCallbackHandler
) -> None:
    """Guarantees a consumer draining `stream_handler` *concurrently* with `run_task` (the
    realistic SSE shape: `async for event in stream_handler` running alongside the task,
    not after it's awaited) always terminates - on every completion path: success, any
    exception raised from inside `execute_agent_run`, or even a failure in the caller
    *before* `execute_agent_run` is ever reached (e.g. `resolve_and_compile`'s "not found"
    `ValueError`, which `execute_agent_run`'s own `try/finally` can't cover since it's
    never entered).

    `execute_agent_run`'s own `finally: stream_handler.done_event.set()` is NOT sufficient
    on its own for this concurrent-drain shape, for a second, independent reason:
    `AsyncStreamingIteratorCallbackHandler._iter_queue_events`'s `await self.queue.get()`
    only wakes up when an item is actually put into the queue - never just because
    `done_event` changed. A consumer already blocked inside that `get()` (which is exactly
    what happens when nothing more arrives right as the run finishes) hangs forever even
    though `done_event` is set correctly. Confirmed by reproducing a real hang live against
    this project's own dev stack (a real OpenRouter call, concurrent drain, `done_event`
    set correctly, consumer still stuck) before this helper existed - a sequential
    "await the run, then drain" caller (this project's own `execute_agent_run`-level tests)
    never hits this, since the queue's final contents are already settled by the time
    draining starts; a concurrent drain always needs this.

    Call this once, right after creating `run_task` (e.g. via `asyncio.create_task(
    run_playground_agent(..., stream_handler=stream_handler))`) and before starting to
    drain `stream_handler` - see `app.main_runtime.runPlaygroundStream` for the canonical
    caller. Any future caller that streams concurrently (gateway/workflow streaming) must
    call this too.
    """

    def _on_done(_task: "asyncio.Task[dict]") -> None:
        stream_handler.done_event.set()
        stream_handler.queue.put_nowait(StreamingEventMessage(data=None))

    run_task.add_done_callback(_on_done)


async def _execute_agent_run_body(
    compiled_definition: dict,
    guardrails: GuardrailsSpec,
    *,
    company_id: str,
    agent_version_id: str,
    source: str,
    input_text: str,
    external_user_id: str | None,
    conversation_id: str | None,
    deployment_id: str | None,
    identity_claims: dict | None,
    identity_sub: str | None,
    run_id: str,
    trace_id: str,
    started: float,
    stream_handler: AsyncStreamingIteratorCallbackHandler | None,
    stream_mode: StreamingMode,
    allow_write_tools: bool,
) -> dict:
    """The actual body of `execute_agent_run`, split out only so the `try/finally` that
    guarantees `stream_handler.done_event` gets set (see `execute_agent_run`'s docstring)
    doesn't force re-indenting this whole function - not a separate concept on its own,
    never call directly."""
    # PRD §7.5: the per-run delegated-identity context every `auth_mode: delegated` HTTP
    # tool this run builds will mint its Authorization JWT from - `act` identifies which
    # deployment/agent-version is acting, `sub` falls back to the memory-scoping
    # external_user_id when the caller didn't resolve a more specific one (session-token
    # claims aren't collected into identity_claims yet for Playground, see
    # run_playground_agent/main_runtime.runPlayground).
    act = f"deployment:{deployment_id}" if deployment_id else f"agent_version:{agent_version_id}"
    effective_sub = identity_sub if identity_sub is not None else (external_user_id or "")
    effective_claims = dict(identity_claims or {})
    if source == "playground":
        effective_claims["playground"] = True
    identity_context = DelegatedIdentityContext(sub=effective_sub, claims=effective_claims, act=act, run_id=run_id)

    agent_def = compiled_definition["agent"]
    connection_id = agent_def["llm"]["connection"]["connection_id"]
    agent_name = agent_def["name"]
    model_name = agent_def["llm"]["model"]

    input_outcome = await run_checks(input_text, guardrails.input, stage="input")
    if input_outcome.blocked:
        latency_ms = int((time.monotonic() - started) * 1000)
        # logs.runs row must exist before logs.guardrail_events.run_id can reference it
        # (FK) - log_run() always goes first, log_guardrail_events() second, everywhere.
        await log_run(
            run_id=run_id,
            company_id=company_id,
            trace_id=trace_id,
            status="blocked",
            source=source,
            agent_version_id=agent_version_id,
            deployment_id=deployment_id,
            agent_name=agent_name,
            model=model_name,
            input_text=input_text,
            output_text=input_outcome.fallback_message,
            latency_ms=latency_ms,
        )
        await log_guardrail_events(company_id, run_id=run_id, events=input_outcome.events)
        await log_run_steps(
            company_id, run_id=run_id, steps=guardrail_run_steps(input_outcome.events, "guardrail_input")
        )
        return {
            "output": input_outcome.fallback_message,
            "trace_id": trace_id,
            "latency_ms": latency_ms,
            "conversation_id": None,
            "guardrail_events": input_outcome.events,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
            "streamed_live": False,
        }

    secret = await decrypt_connection_secret(company_id, connection_id)
    if secret is None:
        raise ValueError(f"no secret stored for connection {connection_id!r}")

    memory = None
    user_id = None
    # Stays None unless memory is actually wired, so a client that sent a
    # conversation_id without the scoping inputs isn't told it was continued.
    resolved_conversation_id = None
    # .get("memory", {}) covers a compiled_definition from before this feature existed (should
    # never happen in practice - see resolve_and_compile's "always recompiled live" note - but
    # matches compiled_definition.get("skills") above's same defensive style); MemorySpec's own
    # field defaults then apply, same single-source-of-truth pattern run_playground_agent uses
    # for guardrails (`GuardrailsSpec.model_validate(compiled_definition["guardrails"])` below).
    memory_spec = MemorySpec.model_validate(compiled_definition.get("memory", {}))
    if memory_spec.enabled and external_user_id and company_id:
        resolved_conversation_id, memory, user_id = await get_or_create_conversation(
            company_id,
            deployment_id,
            external_user_id,
            conversation_id,
            message_limit=memory_spec.message_limit,
            save_mode=memory_spec.save_mode,
        )

    skill_registry = None
    if compiled_definition.get("skills"):
        # Reuses the cached full-company fetch, then scopes down to just this agent's own
        # declared skills - every agent in a company sharing the same registry instance
        # would otherwise see every skill the company owns, not just the ones its own spec
        # lists (PRD §6.6a: "attached to agents in the editor's Skills tab").
        full_registry = await get_console_skill_registry(company_id)
        scoped_skills = [s for s in full_registry.skills if s.name in compiled_definition["skills"]]
        skill_registry = ConsoleSkillRegistry(skills=scoped_skills)

    egress_allowlist = None
    if agent_def["llm"]["connection"].get("api_base"):
        # Only a custom api_base is a company-controlled SSRF vector worth the extra
        # query for - see app.integrations.safe_openai_connection.
        egress_allowlist = await list_egress_allowlist(company_id)

    # Each attached knowledge base has its own embedder connection, independent of the
    # agent's own LLM connection resolved above - so its secret needs its own lookup,
    # same `decrypt_connection_secret` call as the LLM's, just once per KB.
    knowledge_api_keys = {}
    for tool_def in agent_def.get("tools", []):
        if tool_def["type"] != RETRIEVAL_TOOL_TYPE:
            continue
        embedder_connection_id = tool_def["config"]["embedder_connection_id"]
        kb_secret = await decrypt_connection_secret(company_id, embedder_connection_id)
        if kb_secret is None:
            raise ValueError(f"no secret stored for connection {embedder_connection_id!r}")
        knowledge_api_keys[tool_def["id"]] = kb_secret

    # Resolved fresh per run, same as knowledge_api_keys above, so a secret can be
    # rotated without recompiling/republishing the agent - but unlike knowledge_api_keys,
    # a missing entry is NOT an error for a `kind: "http"` tool: a `service`-mode tool is
    # never required to have a secret configured (fail-open, see SafeHttpApiCall's
    # auth_mode branching). `kind: "builtin"` (Tavily/Exa) tools are the opposite - a
    # missing API key makes the tool useless, so this fails closed, same hard-error style
    # as knowledge_api_keys above, rather than letting the real provider's own 401 surface
    # mid-conversation.
    tool_secrets = {}
    for tool_def in agent_def.get("tools", []):
        if tool_def["type"] in BUILTIN_TOOL_TYPES:
            tool_secret = await decrypt_tool_secret(company_id, tool_def["id"])
            if tool_secret is None:
                raise ValueError(f"no secret stored for builtin tool {tool_def['name']!r}")
            tool_secrets[tool_def["id"]] = tool_secret
            continue
        if tool_def.get("auth_mode") != "service":
            continue
        tool_secret = await decrypt_tool_secret(company_id, tool_def["id"])
        if tool_secret is not None:
            tool_secrets[tool_def["id"]] = tool_secret

    agent = await build_agent(
        compiled_definition,
        secret,
        company_id,
        memory=memory,
        skill_registry=skill_registry,
        egress_allowlist=egress_allowlist,
        knowledge_api_keys=knowledge_api_keys,
        identity=identity_context,
        tool_secrets=tool_secrets,
        run_id=run_id,
        allow_write_tools=allow_write_tools,
    )

    usage_collector = _UsageCollector()
    step_collector = _StepCollector()
    callbacks: list[BaseCallbackHandler] = [usage_collector, step_collector]
    # Live streaming is only safe when there's no output guardrail that could later
    # redact/block this text - see execute_agent_run's docstring. `agent.streaming` is a
    # plain mutable field on the already-built Node (dynamiq/nodes/node.py), so setting
    # it here needs no change to app.services.compiler.build_agent.
    streamed_live = stream_handler is not None and not guardrails.output
    if streamed_live:
        agent.streaming = StreamingConfig(enabled=True, mode=stream_mode)
        callbacks.append(stream_handler)

    # `Agent.run` is synchronous and, with memory attached, does blocking
    # psycopg I/O on top of the already-blocking LLM call - run it off the
    # event loop rather than stalling every other concurrent request.
    #
    # user_id/session_id only need to be passed once, at the top level:
    # Agent._prepare_metadata() (dynamiq/nodes/agents/base.py) copies
    # input_data.user_id/session_id into the metadata it hands memory.add()
    # itself - verified against the installed dynamiq==0.65.0 source, no
    # separate `metadata={...}` duplication is needed here.
    run_timeout = _effective_run_timeout(agent_def, get_settings())
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                agent.run,
                input_data={
                    "input": input_outcome.text,
                    "user_id": user_id,
                    "session_id": resolved_conversation_id,
                },
                config=RunnableConfig(callbacks=callbacks),
            ),
            timeout=run_timeout,
        )
        # Agent.run() does NOT raise on an LLM/tool failure (e.g. a rejected API key) -
        # it returns a RunnableResult with status=FAILURE and the error captured in
        # .error instead. Confirmed live: a bad connection secret produced a 200 with
        # empty output until this check was added, silently hiding a real run failure.
        if result.status != RunnableStatus.SUCCESS:
            message = result.error.message if result.error else f"agent run ended with status {result.status}"
            raise RuntimeError(message)
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        usage = usage_collector.total_usage()
        await log_run(
            run_id=run_id,
            company_id=company_id,
            trace_id=trace_id,
            status="error",
            source=source,
            agent_version_id=agent_version_id,
            deployment_id=deployment_id,
            agent_name=agent_name,
            model=model_name,
            input_text=input_outcome.text,
            conversation_id=resolved_conversation_id,
            usage=usage,
            latency_ms=latency_ms,
            error=str(exc),
        )
        await log_guardrail_events(company_id, run_id=run_id, events=input_outcome.events)
        await log_run_steps(
            company_id,
            run_id=run_id,
            steps=[*guardrail_run_steps(input_outcome.events, "guardrail_input"), *step_collector.steps],
        )
        # Surfaced to run_deployment_agent's quota reconciliation below - a run that burned
        # one or more real LLM calls before failing (e.g. a late tool-call error) must still
        # count that spend against the deployment's daily quota, not be refunded in full as
        # if nothing ran at all.
        #
        # Normalized to RuntimeError (unless it already is one, e.g. the explicit
        # non-SUCCESS-status raise above, or a fake-agent test raising RuntimeError
        # directly - both must keep their original message so existing callers/tests that
        # match on it still see it) so that callers can distinguish "the live run itself
        # failed" from a bug in the surrounding gateway code (which raises some other
        # exception type from outside this try block and is never caught/normalized here).
        if isinstance(exc, RuntimeError):
            exc.partial_usage = usage
            raise
        wrapped = RuntimeError(str(exc))
        wrapped.partial_usage = usage
        raise wrapped from exc

    output = result.output.get("content") if result.output else None

    output_outcome = await run_checks(output or "", guardrails.output, stage="output")
    final_output = output_outcome.fallback_message if output_outcome.blocked else output_outcome.text

    latency_ms = int((time.monotonic() - started) * 1000)
    usage = usage_collector.total_usage()

    await log_run(
        run_id=run_id,
        company_id=company_id,
        trace_id=trace_id,
        status="blocked" if output_outcome.blocked else "success",
        source=source,
        agent_version_id=agent_version_id,
        deployment_id=deployment_id,
        agent_name=agent_name,
        model=model_name,
        input_text=input_outcome.text,
        output_text=final_output,
        conversation_id=resolved_conversation_id,
        usage=usage,
        latency_ms=latency_ms,
    )
    await log_guardrail_events(
        company_id, run_id=run_id, events=[*input_outcome.events, *output_outcome.events]
    )
    await log_run_steps(
        company_id,
        run_id=run_id,
        steps=[
            *guardrail_run_steps(input_outcome.events, "guardrail_input"),
            *step_collector.steps,
            *guardrail_run_steps(output_outcome.events, "guardrail_output"),
        ],
    )

    return {
        "output": final_output,
        "trace_id": trace_id,
        "latency_ms": latency_ms,
        "conversation_id": resolved_conversation_id,
        "guardrail_events": [*input_outcome.events, *output_outcome.events],
        "tokens_in": usage.tokens_in,
        "tokens_out": usage.tokens_out,
        "cost_usd": usage.cost_usd,
        "streamed_live": streamed_live,
    }


async def run_playground_agent(
    input_text: str,
    agent_version_id: str,
    role: str,
    company_id: str = "",
    external_user_id: str | None = None,
    conversation_id: str | None = None,
    deployment_id: str | None = None,
    claims: dict | None = None,
    identity_sub: str | None = None,
    stream_handler: AsyncStreamingIteratorCallbackHandler | None = None,
) -> dict:
    """Resolve `agent_version_id`, compile it live, build a real Agent from the
    company's own decrypted connection secret, and run it - with memory when a
    `company_id`/`external_user_id` are available to scope it to. Never counted against
    a deployment's quota (PRD §12: "Daily quota exhausted... Playground is not counted").

    `company_id`/`external_user_id` are optional - when either is missing the run
    proceeds without memory (unchanged from before this was wired up). When both are
    given, `company_id` is trusted as-is even though real auth isn't wired yet
    (`app.core.auth` is still a stub); callers currently source it from the verified
    runtime token, with the trust boundary left as a documented gap until
    `verify_gateway_api_key` needs the same treatment on the gateway side.

    Guardrails (PRD §6.4) are never caller-injectable - they come only from the
    resolved agent version's own compiled definition, same reasoning as why the public
    Playground route itself can't accept a client-supplied guardrails config.

    `claims`/`identity_sub` feed `auth_mode: delegated` tool calls' identity JWT (PRD
    §7.5) - `identity_sub` lets a caller supply the runtime token's own user id as the
    delegated `sub` when no simulated external user was given, distinct from
    `external_user_id`'s memory-scoping role.

    `stream_handler` (PRD Goal #6) is passed straight through to `execute_agent_run` -
    see its docstring for the guardrail-conditional live-streaming behavior.
    """
    compiled_definition = await resolve_and_compile(company_id, agent_version_id, role)
    guardrails = GuardrailsSpec.model_validate(compiled_definition["guardrails"])

    return await execute_agent_run(
        compiled_definition,
        guardrails,
        company_id=company_id,
        agent_version_id=agent_version_id,
        source="playground",
        input_text=input_text,
        external_user_id=external_user_id,
        conversation_id=conversation_id,
        deployment_id=deployment_id,
        identity_claims=claims,
        identity_sub=identity_sub,
        stream_handler=stream_handler,
    )


async def run_deployment_agent(
    company_id: str,
    deployment_id: str,
    role: str,
    input_text: str,
    external_user_id: str | None = None,
    conversation_id: str | None = None,
    claims: dict | None = None,
    stream_handler: AsyncStreamingIteratorCallbackHandler | None = None,
    stream_mode: StreamingMode = StreamingMode.FINAL,
) -> dict:
    """Resolve `deployment_id`'s agent version, compile it live with the deployment's
    own `guardrail_overrides` layered on top (PRD §6.4), reserve quota before running and
    reconcile to actual usage after (PRD §6.3) - the gateway's counterpart to
    `run_playground_agent`. Also checks the company's monthly spend budget
    (`app.services.company_budget`, phase 3) before the per-deployment daily quota -
    cheaper to reject on a company-wide cap first than to size a per-deployment
    reservation for a request that's going to be rejected anyway. Raises
    `QuotaExceededError` when rate-limited/over either quota, same "let it bubble to the
    caller" convention as resolution/compile/secret failures.

    `deployment.allow_write_tools` (PRD §6.5/§8.1) is passed straight through to
    `execute_agent_run` as `allow_write_tools` - the only caller that ever passes anything
    other than the default `True`, since a deployment is the only context with its own
    disallow flag (Playground/eval have no deployment to check).

    `stream_handler`/`stream_mode` (PRD Goal #6, gateway streaming) are passed straight
    through to `execute_agent_run` - see its docstring. Quota reserve/reconcile is
    unaffected either way: reservation happens before the run starts and reconciliation
    reads the final returned dict's actual usage after, same as the non-streaming path -
    `execute_agent_run` itself is still awaited to completion even when a caller is
    concurrently draining `stream_handler` alongside it.
    """
    deployment = await resolve_deployment(company_id, deployment_id)
    if deployment is None:
        raise ValueError(f"deployment_id {deployment_id!r} not found for this company")
    if not deployment.enabled:
        raise ValueError(f"deployment_id {deployment_id!r} is disabled")

    compiled_definition = await resolve_and_compile(company_id, deployment.agent_version_id, role)
    guardrails = _merge_guardrail_overrides(compiled_definition, deployment.guardrail_overrides)

    rate_limit = await check_rate_limit(deployment_id, deployment.rate_limit_per_min)
    if not rate_limit.allowed:
        raise QuotaExceededError("rate_limit", reset_at=None)

    budget_check = await check_monthly_budget_not_exceeded(company_id)
    if not budget_check.allowed:
        raise QuotaExceededError(budget_check.reason or "monthly_budget", reset_at=budget_check.reset_at)

    model_name = compiled_definition["agent"]["llm"]["model"]
    max_tokens = compiled_definition["agent"]["llm"].get("max_tokens")
    reservation = await reserve_quota(
        deployment_id, model_name, max_tokens, deployment.daily_token_limit, deployment.daily_cost_limit_usd
    )
    if not reservation.allowed:
        raise QuotaExceededError(reservation.reason or "quota_exceeded", reset_at=reservation.reset_at)

    result: dict | None = None
    partial_usage: RunUsage | None = None
    try:
        result = await execute_agent_run(
            compiled_definition,
            guardrails,
            company_id=company_id,
            agent_version_id=deployment.agent_version_id,
            source="api",
            input_text=input_text,
            external_user_id=external_user_id,
            conversation_id=conversation_id,
            deployment_id=deployment_id,
            identity_claims=claims,
            stream_handler=stream_handler,
            stream_mode=stream_mode,
            allow_write_tools=deployment.allow_write_tools,
        )
        return result
    except Exception as exc:
        # execute_agent_run attaches whatever real usage it collected before failing
        # (e.g. one or more completed LLM calls before a later tool-call error) - fall
        # back to "nothing ran" only when it genuinely never got that far.
        partial_usage = getattr(exc, "partial_usage", None)
        raise
    finally:
        if result is not None:
            actual_tokens = (result.get("tokens_in") or 0) + (result.get("tokens_out") or 0)
            actual_cost_usd = result.get("cost_usd") or 0
        elif partial_usage is not None:
            actual_tokens = (partial_usage.tokens_in or 0) + (partial_usage.tokens_out or 0)
            actual_cost_usd = partial_usage.cost_usd or 0
        else:
            actual_tokens = 0
            actual_cost_usd = 0
        await reconcile_quota(
            deployment_id,
            reservation.reserved_tokens,
            reservation.reserved_cost_usd,
            actual_tokens=actual_tokens,
            actual_cost_usd=actual_cost_usd,
            today=reservation.today,
            ttl=reservation.ttl,
        )
