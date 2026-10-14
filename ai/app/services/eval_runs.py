"""`runtime.eval_runs`/`logs.eval_results` repository (PRD §6.7/§8.1).

Split from `eval_runner.py` the same way `knowledge_bases.py` is split from
`kb_indexer.py`: this module owns the row CRUD, `eval_runner.py` owns the actual
execution logic that calls into it.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa

from app.core.db import get_company_session
from app.db.tables import eval_results_table, eval_runs_table


@dataclass(frozen=True)
class EvalRunRow:
    id: str
    company_id: str
    agent_version_id: str
    dataset_id: str
    role: str
    metrics: list[str]
    judge_model: str | None
    status: str
    est_cost_usd: float | None
    summary: dict | None
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EvalResultInput:
    dataset_item_id: str
    run_id: str | None
    scores: dict[str, float] | None
    reasons: dict[str, str | None] | None
    error: str | None


def _row_to_eval_run(row: dict) -> EvalRunRow:
    return EvalRunRow(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        agent_version_id=str(row["agent_version_id"]),
        dataset_id=str(row["dataset_id"]),
        role=row["role"],
        metrics=row["metrics"],
        judge_model=row["judge_model"],
        status=row["status"],
        est_cost_usd=float(row["est_cost_usd"]) if row["est_cost_usd"] is not None else None,
        summary=row["summary"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def create_eval_run(
    company_id: str,
    agent_version_id: str,
    dataset_id: str,
    role: str,
    metrics: list[str],
    judge_model: str | None,
    est_cost_usd: float | None,
) -> EvalRunRow:
    """Inserts a new `eval_runs` row with `status="queued"` - called before the ARQ job
    is enqueued (same "row exists before enqueue" ordering `upsert_kb_document` +
    `enqueueKbDocumentIndexing` already establish for KB indexing)."""
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                eval_runs_table.insert()
                .values(
                    company_id=uuid.UUID(company_id),
                    agent_version_id=uuid.UUID(agent_version_id),
                    dataset_id=uuid.UUID(dataset_id),
                    role=role,
                    metrics=metrics,
                    judge_model=judge_model,
                    status="queued",
                    est_cost_usd=est_cost_usd,
                )
                .returning(eval_runs_table)
            )
        ).mappings().one()
    return _row_to_eval_run(dict(row))


async def get_eval_run(company_id: str, eval_run_id: str) -> EvalRunRow | None:
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                sa.select(eval_runs_table).where(eval_runs_table.c.id == uuid.UUID(eval_run_id))
            )
        ).mappings().one_or_none()
    return None if row is None else _row_to_eval_run(dict(row))


async def update_eval_run_status(
    company_id: str,
    eval_run_id: str,
    *,
    status: str,
    summary: dict | None = None,
    error: str | None = None,
) -> None:
    """Mirrors `knowledge_bases.py::update_kb_document_status`'s exact shape: keyword-
    only status/metadata args after a positional id, single `UPDATE`."""
    async with get_company_session(company_id) as session:
        await session.execute(
            eval_runs_table.update()
            .where(eval_runs_table.c.id == uuid.UUID(eval_run_id))
            .values(status=status, summary=summary, error=error, updated_at=sa.func.now())
        )


async def insert_eval_results(company_id: str, eval_run_id: str, results: list[EvalResultInput]) -> None:
    """Bulk-inserts every dataset item's result row for a run in one statement rather
    than N round trips - called once, at the end of `eval_runner.run_eval`."""
    if not results:
        return

    async with get_company_session(company_id) as session:
        await session.execute(
            eval_results_table.insert(),
            [
                {
                    "company_id": uuid.UUID(company_id),
                    "eval_run_id": uuid.UUID(eval_run_id),
                    "dataset_item_id": uuid.UUID(result.dataset_item_id),
                    "run_id": uuid.UUID(result.run_id) if result.run_id else None,
                    "scores": result.scores,
                    "reasons": result.reasons,
                    "error": result.error,
                }
                for result in results
            ],
        )
