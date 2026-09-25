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
from uuid import uuid4

from app.integrations.dynamiq_adapter import build_llm
from app.services.conversations import get_or_create_conversation


async def run_playground_agent(
    input_text: str,
    company_id: str = "",
    external_user_id: str | None = None,
    conversation_id: str | None = None,
    deployment_id: str | None = None,
) -> dict:
    """Build a minimal Dynamiq Agent and run it, with memory when a
    `company_id`/`external_user_id` are available to scope it to.

    Returns `{"output": str, "trace_id": str, "latency_ms": int,
    "conversation_id": str | None}`.

    Raises whatever the underlying Dynamiq/OpenAI call raises (e.g. missing
    API key, provider error) - callers (see `app.main_runtime`) are
    responsible for turning that into an HTTP error response.

    `company_id`/`external_user_id` are optional - when either is missing the
    run proceeds without memory (same behaviour as before this was wired up).
    When both are given, `company_id` is trusted as-is even though real auth
    isn't wired yet (`app.core.auth` is still a stub); callers currently
    source it from the `X-Company-Id` header, matching the existing
    internal-API convention, with the trust boundary left as a documented gap
    until `verify_runtime_token`/`verify_gateway_api_key` replace it.
    """
    from dynamiq.nodes.agents import Agent

    trace_id = str(uuid4())
    started = time.monotonic()

    _connection, llm = build_llm(
        "dynamiq.connections.OpenAI",
        {"api_key": os.environ.get("OPENAI_API_KEY"), "model": "gpt-4o-mini"},
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
        name="playground-agent",
        llm=llm,
        tools=[],
        role="You are a helpful assistant.",
        max_loops=3,
        memory=memory,
    )

    # `Agent.run` is synchronous and, with memory attached, does blocking
    # psycopg I/O on top of the already-blocking LLM call - run it off the
    # event loop rather than stalling every other concurrent request.
    #
    # user_id/session_id only need to be passed once, at the top level:
    # Agent._prepare_metadata() (dynamiq/nodes/agents/base.py) copies
    # input_data.user_id/session_id into the metadata it hands memory.add()
    # itself - verified against the installed dynamiq==0.65.0 source, no
    # separate `metadata={...}` duplication is needed here.
    result = await asyncio.to_thread(
        agent.run,
        input_data={
            "input": input_text,
            "user_id": user_id,
            "session_id": resolved_conversation_id,
        },
    )
    output = result.output.get("content") if result.output else None

    latency_ms = int((time.monotonic() - started) * 1000)

    return {
        "output": output,
        "trace_id": trace_id,
        "latency_ms": latency_ms,
        "conversation_id": resolved_conversation_id,
    }
