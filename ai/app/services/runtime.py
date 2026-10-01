"""Playground run service - the one fully real, load-bearing piece of this skeleton: it
resolves a company's own agent version, compiles it live, and runs a real Dynamiq
`Agent` built from a real decrypted connection secret.

Memory (PRD §6.2) and Guardrails (PRD §6.4) are both wired for real - see
`app.services.conversations` / `app.services.guardrails`.
"""
import asyncio
import time
from typing import Any
from uuid import uuid4

from dynamiq.callbacks.base import BaseCallbackHandler
from dynamiq.runnables.base import RunnableConfig, RunnableStatus

from app.services.agent_spec import GuardrailsSpec
from app.services.agent_versions import resolve_agent_version
from app.services.compiler import build_agent, compile_spec
from app.services.connection_secrets import decrypt_connection_secret
from app.services.conversations import get_or_create_conversation
from app.services.guardrails import log_guardrail_events, run_checks
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
    since a runtime token's agent_version_id is never required to be published
    (RuntimeTokenController::issueRuntimeToken only checks it exists in the caller's
    company - Playground is explicitly a pre-publish testing tool). Raises ValueError
    (not found / compile failure) - callers let it bubble up to the existing
    "genuine failure -> 502" handling in app.main_runtime, no new error shape needed.
    """
    version = await resolve_agent_version(company_id, agent_version_id)
    if version is None:
        raise ValueError(f"agent_version_id {agent_version_id!r} not found for this company")

    result = await compile_spec(version.spec, role, company_id)
    if not result["ok"]:
        raise ValueError(f"agent version {agent_version_id!r} failed to compile: {result['errors']}")

    return result["compiled_definition"]


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
    `company_id`/`external_user_id` are available to scope it to.

    Returns `{"output": str, "trace_id": str, "latency_ms": int,
    "conversation_id": str | None, "guardrail_events": list}`.

    Raises whatever resolution/compilation/secret-lookup or the underlying Dynamiq/
    OpenAI call itself raises - callers (see `app.main_runtime`) are responsible for
    turning that into an HTTP error response. A `logs.runs` row with status="error" is
    still written before re-raising, for every failure past the point a run_id exists.

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
    run_id = str(uuid4())
    trace_id = str(uuid4())
    started = time.monotonic()

    compiled_definition = await _resolve_and_compile(company_id, agent_version_id, role)
    agent_def = compiled_definition["agent"]
    connection_id = agent_def["llm"]["connection"]["connection_id"]
    agent_name = agent_def["name"]
    model_name = agent_def["llm"]["model"]
    guardrails = GuardrailsSpec.model_validate(compiled_definition["guardrails"])

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

    agent = build_agent(compiled_definition, secret, memory=memory)

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
        result = await asyncio.to_thread(
            agent.run,
            input_data={
                "input": input_outcome.text,
                "user_id": user_id,
                "session_id": resolved_conversation_id,
            },
            config=RunnableConfig(callbacks=[usage_collector]),
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
        await log_run(
            run_id=run_id,
            company_id=company_id,
            trace_id=trace_id,
            status="error",
            agent_version_id=agent_version_id,
            deployment_id=deployment_id,
            agent_name=agent_name,
            model=model_name,
            input_text=input_outcome.text,
            conversation_id=resolved_conversation_id,
            usage=usage_collector.total_usage(),
            latency_ms=latency_ms,
            error=str(exc),
        )
        await log_guardrail_events(company_id, run_id=run_id, events=input_outcome.events)
        raise

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
