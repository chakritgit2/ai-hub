"""Agent run execution - the one fully real, load-bearing piece of this skeleton. Both
entry points resolve a company's own agent version, compile it live, and run a real
Dynamiq `Agent` built from a real decrypted connection secret:

- `run_playground_agent` - PRD §4.4-B, a verified runtime token's `agent_version_id`/
  `role`, no quota (Playground is never counted against a deployment's daily budget).
- `run_deployment_agent` - PRD §4.4-C/§6.3, a gateway API key/session token's resolved
  deployment, with the deployment's own `guardrail_overrides` layered on top of the
  agent version's guardrails and real quota reservation/reconciliation
  (`app.services.quotas`) wrapped around the run.

Both share the same core (`_execute_agent_run`): guardrail checks, memory (PRD §6.2),
usage tracking, and `logs.runs`/`logs.guardrail_events` writes.
"""
import asyncio
import time
from typing import Any
from uuid import uuid4

from dynamiq.callbacks.base import BaseCallbackHandler
from dynamiq.runnables.base import RunnableConfig, RunnableStatus

from app.core.config import get_settings
from app.integrations.skill_registry import ConsoleSkillRegistry, get_console_skill_registry
from app.services.agent_spec import GuardrailsSpec
from app.services.agent_versions import resolve_agent_version
from app.services.compiler import build_agent, compile_spec
from app.services.connection_secrets import decrypt_connection_secret
from app.services.conversations import get_or_create_conversation
from app.services.deployments import resolve_deployment
from app.services.guardrails import log_guardrail_events, run_checks
from app.services.quotas import QuotaExceededError, check_rate_limit, reconcile_quota, reserve_quota
from app.services.runs import RunUsage, log_run


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


async def _resolve_and_compile(company_id: str, agent_version_id: str, role: str) -> dict:
    """Resolves `agent_version_id` within `company_id` and compiles its *current* spec
    live (PRD §4.4-B) - never the `compiled_definition` a Publish may have snapshotted,
    since neither a runtime token's agent_version_id nor a deployment's is required to
    be published (RuntimeTokenController::issueRuntimeToken only checks it exists in the
    caller's company). Raises ValueError (not found / compile failure) - callers let it
    bubble up to the existing "genuine failure -> 502" handling in app.main_runtime/
    app.main_gateway, no new error shape needed.
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


async def _execute_agent_run(
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
) -> dict:
    """Shared core: guardrail checks, memory, build+run the real Agent, usage tracking,
    `logs.runs`/`logs.guardrail_events` writes. Raises on resolution-adjacent failures
    (missing secret) and on the underlying Dynamiq/OpenAI call itself - callers are
    responsible for turning that into an HTTP error response. A `logs.runs` row with
    status="error" is still written before re-raising, for every failure past the point
    a run_id exists.

    Returns `{"output": str, "trace_id": str, "latency_ms": int,
    "conversation_id": str | None, "guardrail_events": list, "tokens_in": int | None,
    "tokens_out": int | None, "cost_usd": float | None}`.
    """
    run_id = str(uuid4())
    trace_id = str(uuid4())
    started = time.monotonic()

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
        return {
            "output": input_outcome.fallback_message,
            "trace_id": trace_id,
            "latency_ms": latency_ms,
            "conversation_id": None,
            "guardrail_events": input_outcome.events,
            "tokens_in": None,
            "tokens_out": None,
            "cost_usd": None,
        }

    secret = await decrypt_connection_secret(company_id, connection_id)
    if secret is None:
        raise ValueError(f"no secret stored for connection {connection_id!r}")

    memory = None
    user_id = None
    # Stays None unless memory is actually wired, so a client that sent a
    # conversation_id without the scoping inputs isn't told it was continued.
    resolved_conversation_id = None
    if external_user_id and company_id:
        resolved_conversation_id, memory, user_id = await get_or_create_conversation(
            company_id, deployment_id, external_user_id, conversation_id
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

    agent = build_agent(compiled_definition, secret, memory=memory, skill_registry=skill_registry)

    usage_collector = _UsageCollector()

    # `Agent.run` is synchronous and, with memory attached, does blocking
    # psycopg I/O on top of the already-blocking LLM call - run it off the
    # event loop rather than stalling every other concurrent request.
    #
    # user_id/session_id only need to be passed once, at the top level:
    # Agent._prepare_metadata() (dynamiq/nodes/agents/base.py) copies
    # input_data.user_id/session_id into the metadata it hands memory.add()
    # itself - verified against the installed dynamiq==0.65.0 source, no
    # separate `metadata={...}` duplication is needed here.
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                agent.run,
                input_data={
                    "input": input_outcome.text,
                    "user_id": user_id,
                    "session_id": resolved_conversation_id,
                },
                config=RunnableConfig(callbacks=[usage_collector]),
            ),
            timeout=get_settings().RUN_TIMEOUT_SECONDS,
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

    return {
        "output": final_output,
        "trace_id": trace_id,
        "latency_ms": latency_ms,
        "conversation_id": resolved_conversation_id,
        "guardrail_events": [*input_outcome.events, *output_outcome.events],
        "tokens_in": usage.tokens_in,
        "tokens_out": usage.tokens_out,
        "cost_usd": usage.cost_usd,
    }


async def run_playground_agent(
    input_text: str,
    agent_version_id: str,
    role: str,
    company_id: str = "",
    external_user_id: str | None = None,
    conversation_id: str | None = None,
    deployment_id: str | None = None,
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
    """
    compiled_definition = await _resolve_and_compile(company_id, agent_version_id, role)
    guardrails = GuardrailsSpec.model_validate(compiled_definition["guardrails"])

    return await _execute_agent_run(
        compiled_definition,
        guardrails,
        company_id=company_id,
        agent_version_id=agent_version_id,
        source="playground",
        input_text=input_text,
        external_user_id=external_user_id,
        conversation_id=conversation_id,
        deployment_id=deployment_id,
    )


async def run_deployment_agent(
    company_id: str,
    deployment_id: str,
    role: str,
    input_text: str,
    external_user_id: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Resolve `deployment_id`'s agent version, compile it live with the deployment's
    own `guardrail_overrides` layered on top (PRD §6.4), reserve quota before running and
    reconcile to actual usage after (PRD §6.3) - the gateway's counterpart to
    `run_playground_agent`. Raises `QuotaExceededError` when rate-limited/over quota, same
    "let it bubble to the caller" convention as resolution/compile/secret failures.
    """
    deployment = await resolve_deployment(company_id, deployment_id)
    if deployment is None:
        raise ValueError(f"deployment_id {deployment_id!r} not found for this company")
    if not deployment.enabled:
        raise ValueError(f"deployment_id {deployment_id!r} is disabled")

    compiled_definition = await _resolve_and_compile(company_id, deployment.agent_version_id, role)
    guardrails = _merge_guardrail_overrides(compiled_definition, deployment.guardrail_overrides)

    rate_limit = await check_rate_limit(deployment_id, deployment.rate_limit_per_min)
    if not rate_limit.allowed:
        raise QuotaExceededError("rate_limit", reset_at=None)

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
        result = await _execute_agent_run(
            compiled_definition,
            guardrails,
            company_id=company_id,
            agent_version_id=deployment.agent_version_id,
            source="api",
            input_text=input_text,
            external_user_id=external_user_id,
            conversation_id=conversation_id,
            deployment_id=deployment_id,
        )
        return result
    except Exception as exc:
        # _execute_agent_run attaches whatever real usage it collected before failing
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
