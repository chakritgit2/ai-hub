"""Writes a completed agent run to `logs.runs` (PRD §6.8/§6.9).

One row per `run_playground_agent` call - status is "success", "error" (the agent/LLM
call itself raised) or "blocked" (a guardrail rejected the input or output); "interrupted"/
"truncated"/"cancelled" are part of the DB enum but unreachable from this skeleton, which
has no streaming/cancellation path yet.
"""
import uuid
from dataclasses import dataclass

from app.core.db import get_company_session
from app.core.metrics import run_cost_usd_total, run_latency_ms, run_tokens_total, runs_total
from app.db.tables import runs_table


@dataclass(frozen=True)
class RunUsage:
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


async def log_run(
    *,
    run_id: str,
    company_id: str,
    trace_id: str,
    status: str,
    source: str = "playground",
    agent_version_id: str | None = None,
    workflow_version_id: str | None = None,
    deployment_id: str | None = None,
    conversation_id: str | None = None,
    agent_name: str | None = None,
    model: str | None = None,
    input_text: str | None = None,
    output_text: str | None = None,
    usage: RunUsage | None = None,
    guardrail_cost_usd: float = 0,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    """The `logs.runs` write is a no-op for a blank `company_id` (no company context to
    scope it to - same convention as `app.services.conversations`/
    `app.services.guardrails`), but the metrics below carry no company label and have no
    such reason to skip, so they're recorded regardless."""
    usage = usage or RunUsage()

    if company_id:
        async with get_company_session(company_id) as session:
            await session.execute(
                runs_table.insert(),
                {
                    "id": uuid.UUID(run_id),
                    "company_id": uuid.UUID(company_id),
                    "agent_version_id": uuid.UUID(agent_version_id) if agent_version_id else None,
                    "workflow_version_id": uuid.UUID(workflow_version_id) if workflow_version_id else None,
                    "deployment_id": uuid.UUID(deployment_id) if deployment_id else None,
                    "conversation_id": uuid.UUID(conversation_id) if conversation_id else None,
                    "source": source,
                    "status": status,
                    "agent_name": agent_name,
                    "model": model,
                    "input": input_text,
                    "output": output_text,
                    "tokens_in": usage.tokens_in,
                    "tokens_out": usage.tokens_out,
                    "cost_usd": usage.cost_usd,
                    "guardrail_cost_usd": guardrail_cost_usd,
                    "latency_ms": latency_ms,
                    "trace_id": trace_id,
                    "error": error,
                },
            )

    runs_total.labels(status=status, source=source).inc()
    if usage.tokens_in:
        run_tokens_total.labels(direction="in").inc(usage.tokens_in)
    if usage.tokens_out:
        run_tokens_total.labels(direction="out").inc(usage.tokens_out)
    if usage.cost_usd:
        run_cost_usd_total.inc(usage.cost_usd)
    if latency_ms is not None:
        run_latency_ms.labels(status=status).observe(latency_ms)
