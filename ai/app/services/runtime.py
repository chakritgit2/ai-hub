"""Playground/gateway run service - the one fully real, load-bearing piece of
this skeleton: it builds and runs an actual Dynamiq `Agent`.

Real production behaviour (PRD §4.4-B/C, §6.1, §6.2) resolves the agent's
compiled definition, connection secrets and guardrails from Postgres
per-company; this skeleton hardcodes a single OpenAI-backed agent so the
`/ai/v1/playground/run` route is genuinely executable end-to-end. Memory
(PRD §6.2) *is* wired for real - see `app.services.conversations`.
"""
import asyncio
import os
import time
from typing import Any
from uuid import uuid4

from dynamiq.callbacks.base import BaseCallbackHandler
from dynamiq.runnables.base import RunnableConfig

from app.integrations.dynamiq_adapter import build_llm
from app.services.agent_spec import GuardrailsSpec
from app.services.conversations import get_or_create_conversation
from app.services.guardrails import log_guardrail_events, run_checks
from app.services.runs import RunUsage, log_run

AGENT_NAME = "playground-agent"
MODEL_NAME = "gpt-4o-mini"


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


async def run_playground_agent(
    input_text: str,
    company_id: str = "",
    external_user_id: str | None = None,
    conversation_id: str | None = None,
    deployment_id: str | None = None,
    guardrails: GuardrailsSpec | None = None,
) -> dict:
    """Build a minimal Dynamiq Agent and run it, with memory when a
    `company_id`/`external_user_id` are available to scope it to.

    Returns `{"output": str, "trace_id": str, "latency_ms": int,
    "conversation_id": str | None, "guardrail_events": list}`.

    Raises whatever the underlying Dynamiq/OpenAI call raises (e.g. missing
    API key, provider error) - callers (see `app.main_runtime`) are
    responsible for turning that into an HTTP error response. A `logs.runs`
    row with status="error" is still written before re-raising.

    `company_id`/`external_user_id` are optional - when either is missing the
    run proceeds without memory (same behaviour as before this was wired up).
    When both are given, `company_id` is trusted as-is even though real auth
    isn't wired yet (`app.core.auth` is still a stub); callers currently
    source it from the `X-Company-Id` header, matching the existing
    internal-API convention, with the trust boundary left as a documented gap
    until `verify_runtime_token`/`verify_gateway_api_key` replace it.

    `guardrails` (PRD §6.4) is optional and defaults to no checks at all -
    this skeleton has no way to resolve a deployment's/agent version's real
    compiled guardrails config yet (Playground always runs the one hardcoded
    agent above, not a real compiled definition), so nothing passes this in
    today; it exists so the guardrail engine itself is callable and tested
    end-to-end ahead of that larger, separate piece of work. When a block
    fires on the input side, the agent is never called at all - no LLM cost
    for a request that was always going to be rejected.

    Every call writes one `logs.runs` row (PRD §6.8) - `agent_version_id`/
    `deployment_id` stay NULL for now since Playground doesn't resolve a real
    compiled agent version yet (same pre-existing, separately-tracked gap as
    above); `source` is hardcoded "playground" since that's the only real
    caller today.
    """
    from dynamiq.nodes.agents import Agent

    run_id = str(uuid4())
    trace_id = str(uuid4())
    started = time.monotonic()
    guardrails = guardrails or GuardrailsSpec()

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
            agent_name=AGENT_NAME,
            model=MODEL_NAME,
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

    _connection, llm = build_llm(
        "dynamiq.connections.OpenAI",
        {"api_key": os.environ.get("OPENAI_API_KEY"), "model": MODEL_NAME},
    )

    memory = None
    user_id = None
    # Stays None unless memory is actually wired, so a client that sent a
    # conversation_id without the scoping inputs isn't told it was continued.
    resolved_conversation_id = None
    if external_user_id and company_id:
        resolved_conversation_id, memory, user_id = await get_or_create_conversation(
            company_id, deployment_id, external_user_id, conversation_id
        )

    agent = Agent(
        name=AGENT_NAME,
        llm=llm,
        tools=[],
        role="You are a helpful assistant.",
        max_loops=3,
        memory=memory,
    )

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
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        await log_run(
            run_id=run_id,
            company_id=company_id,
            trace_id=trace_id,
            status="error",
            agent_name=AGENT_NAME,
            model=MODEL_NAME,
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

    await log_run(
        run_id=run_id,
        company_id=company_id,
        trace_id=trace_id,
        status="blocked" if output_outcome.blocked else "success",
        agent_name=AGENT_NAME,
        model=MODEL_NAME,
        input_text=input_outcome.text,
        output_text=final_output,
        conversation_id=resolved_conversation_id,
        usage=usage_collector.total_usage(),
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
    }
