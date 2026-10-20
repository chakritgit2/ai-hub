"""`runtime.tool_approvals` repository (PRD §6.5/§8.1).

Only the decide half lives here this round - nothing yet creates a `pending` row (that
requires hooking the actual write-tool call path in `compiler.py`/`SafeHttpApiCall`,
deliberately deferred). `console-api` reads the list directly via its own
`runtime.tool_approvals` SELECT grant (db/migrations/post/025_tool_approvals_console_grants.sql),
same pattern as `eval_runs.py`/`EvalsController::listEvalRuns`.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa

from app.core.db import get_company_session
from app.db.tables import tool_approvals_table


@dataclass(frozen=True)
class ApprovalRow:
    id: str
    company_id: str
    run_id: str
    tool_id: str
    request: dict
    status: str
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


class ApprovalNotFoundError(Exception):
    pass


class ApprovalAlreadyDecidedError(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(f"approval already decided: {status}")


def _row_to_approval(row: dict) -> ApprovalRow:
    return ApprovalRow(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        run_id=str(row["run_id"]),
        tool_id=str(row["tool_id"]),
        request=row["request"],
        status=row["status"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        created_at=row["created_at"],
    )


async def get_approval(company_id: str, approval_id: str) -> ApprovalRow | None:
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                sa.select(tool_approvals_table).where(tool_approvals_table.c.id == uuid.UUID(approval_id))
            )
        ).mappings().one_or_none()
    return None if row is None else _row_to_approval(dict(row))


async def decide_approval(
    company_id: str,
    approval_id: str,
    decision: Literal["approved", "rejected"],
    decided_by: str,
) -> ApprovalRow:
    """Flips a `pending` row to `approved`/`rejected`. Raises `ApprovalNotFoundError` if no
    row with this id exists for this company, or `ApprovalAlreadyDecidedError` (carrying
    the row's current status) if it exists but isn't `pending` anymore - a second decision
    on the same approval (e.g. a double-click, or the console and the app racing) must not
    silently overwrite the first one."""
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                tool_approvals_table.update()
                .where(tool_approvals_table.c.id == uuid.UUID(approval_id))
                .where(tool_approvals_table.c.status == "pending")
                .values(status=decision, decided_by=decided_by, decided_at=sa.func.now())
                .returning(tool_approvals_table)
            )
        ).mappings().one_or_none()

        if row is not None:
            return _row_to_approval(dict(row))

        existing = (
            await session.execute(
                sa.select(tool_approvals_table).where(tool_approvals_table.c.id == uuid.UUID(approval_id))
            )
        ).mappings().one_or_none()

    if existing is None:
        raise ApprovalNotFoundError()
    raise ApprovalAlreadyDecidedError(existing["status"])
