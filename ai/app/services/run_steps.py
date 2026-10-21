"""Writes per-step trace rows to `logs.run_steps` (PRD §6.8/§6.9 Trace tab).

One row per LLM call / tool call within a run (collected live by
`app.services.runtime._StepCollector`), plus one row per triggered guardrail check
(`guardrail_run_steps`, converting the same `CheckEvent` list `log_guardrail_events`
already writes to `logs.guardrail_events`) - same write, different table, so the Trace
tab can show guardrail checks interleaved with LLM/tool steps in run order.
"""
import uuid
from dataclasses import dataclass, field

from app.core.db import get_company_session
from app.db.tables import run_steps_table
from app.services.guardrails import CheckEvent


@dataclass(frozen=True)
class RunStep:
    step_type: str  # "guardrail_input" | "llm" | "tool_call" | "guardrail_output"
    name: str | None
    status: str  # "success" | "error" (node step) or "block" | "mask" | "flag" (guardrail step)
    input_text: str | None = None
    output_text: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    error: str | None = None
    detail: dict = field(default_factory=dict)


def guardrail_run_steps(events: list[CheckEvent], step_type: str) -> list[RunStep]:
    """Converts triggered guardrail checks into `RunStep`s - `step_type` is
    "guardrail_input" or "guardrail_output" depending on which stage's events are
    passed in (the `CheckEvent.stage` field is for `logs.guardrail_events` only, not
    reused here, since a stage's events are always converted together by the caller)."""
    return [
        RunStep(step_type=step_type, name=event.check, status=event.action, detail=event.detail)
        for event in events
    ]


async def log_run_steps(company_id: str, run_id: str, steps: list[RunStep]) -> None:
    """A no-op for an empty `steps` list or a blank `company_id` (no company context to
    scope the write to - same convention as `app.services.guardrails.log_guardrail_events`)."""
    if not steps or not company_id:
        return

    async with get_company_session(company_id) as session:
        await session.execute(
            run_steps_table.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "company_id": uuid.UUID(company_id),
                    "run_id": uuid.UUID(run_id),
                    "step_index": index,
                    "step_type": step.step_type,
                    "name": step.name,
                    "status": step.status,
                    "input": step.input_text,
                    "output": step.output_text,
                    "tokens_in": step.tokens_in,
                    "tokens_out": step.tokens_out,
                    "cost_usd": step.cost_usd,
                    "latency_ms": step.latency_ms,
                    "error": step.error,
                    "detail": step.detail,
                }
                for index, step in enumerate(steps)
            ],
        )
