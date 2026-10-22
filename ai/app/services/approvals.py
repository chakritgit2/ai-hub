"""`runtime.tool_approvals` repository (PRD §6.5/§8.1).

Both halves live here: `decide_approval` (console/app-driven approve/reject) and
`create_pending_approval`/`wait_for_decision`/`enforce_write_tool_gate` (the write-tool
call path itself, hooked in from `app.integrations.safe_http_tool.SafeHttpApiCall` and
`app.integrations.safe_builtin_tools._SafeBuiltinSearchTool`). `console-api` reads the
list directly via its own `runtime.tool_approvals` SELECT grant (db/migrations/post/
025_tool_approvals_console_grants.sql), same pattern as `eval_runs.py`/
`EvalsController::listEvalRuns`.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa

from app.core.config import get_settings
from app.core.db import get_company_session
from app.db.tables import tool_approvals_table

_POLL_INTERVAL_SECONDS = 2


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


class WriteToolsNotAllowedError(Exception):
    """Raised by `enforce_write_tool_gate` when the calling deployment's own
    `allow_write_tools` is `False` (PRD: write tools "must be allowed by the
    deployment") - rejected before a `pending` row is ever created, since there's
    nothing for a human to decide here."""


class ApprovalRejectedError(Exception):
    def __init__(self, approval_id: str):
        self.approval_id = approval_id
        super().__init__(f"approval {approval_id} was rejected")


class ApprovalExpiredError(Exception):
    def __init__(self, approval_id: str):
        self.approval_id = approval_id
        super().__init__(f"approval {approval_id} expired waiting for a decision")


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
    decision: Literal["approved", "rejected", "expired"],
    decided_by: str,
) -> ApprovalRow:
    """Flips a `pending` row to `approved`/`rejected`/`expired`. Raises
    `ApprovalNotFoundError` if no row with this id exists for this company, or
    `ApprovalAlreadyDecidedError` (carrying the row's current status) if it exists but
    isn't `pending` anymore - a second decision on the same approval (e.g. a double-click,
    the console and the app racing, or `wait_for_decision`'s own timeout-expiry racing a
    human's just-in-time decision) must not silently overwrite the first one.

    `"expired"` is only ever passed by `wait_for_decision` below (console/app-facing
    callers - `ApprovalsController::decideApproval`, both `decideApproval` routes in
    `main_runtime.py`/`main_gateway.py` - only ever send `"approved"`/`"rejected"`,
    validated at their own request-body level)."""
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


async def create_pending_approval(company_id: str, run_id: str, tool_id: str, request: dict) -> str:
    """Inserts a new `pending` row for one `write`-access tool call attempt. `request`
    must be built from the LLM's own tool-call input only (e.g.
    `input_data.model_dump(mode="json")`) - never from a fully-built outbound request,
    which could carry a `service`-mode secret or a `delegated` JWT in its headers that a
    company's own console users (who can read this table) must never see."""
    async with get_company_session(company_id) as session:
        result = await session.execute(
            tool_approvals_table.insert()
            .values(
                company_id=uuid.UUID(company_id),
                run_id=uuid.UUID(run_id),
                tool_id=uuid.UUID(tool_id),
                request=request,
            )
            .returning(tool_approvals_table.c.id)
        )
        new_id = result.scalar_one()
    return str(new_id)


async def wait_for_decision(company_id: str, approval_id: str) -> str:
    """Polls `approval_id` every `_POLL_INTERVAL_SECONDS` until it's no longer `pending`
    or `Settings.APPROVAL_TIMEOUT_SECONDS` (PRD §6.5: 5-minute window) elapses, whichever
    comes first. On timeout, flips the row to `expired` itself via `decide_approval` - if
    a human's own decision lands in the same instant, `ApprovalAlreadyDecidedError` hands
    back whichever decision actually won the race, rather than this silently overwriting
    it. Returns the row's final status (`"approved"`/`"rejected"`/`"expired"`); never
    raises on a normal outcome - callers (`enforce_write_tool_gate`) interpret the
    returned status.
    """
    deadline = time.monotonic() + get_settings().APPROVAL_TIMEOUT_SECONDS
    while True:
        approval = await get_approval(company_id, approval_id)
        if approval.status != "pending":
            return approval.status
        if time.monotonic() >= deadline:
            try:
                expired = await decide_approval(company_id, approval_id, "expired", decided_by="system:timeout")
                return expired.status
            except ApprovalAlreadyDecidedError as exc:
                return exc.status
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def enforce_write_tool_gate(
    company_id: str,
    run_id: str,
    tool_id: str,
    allow_write_tools: bool,
    request: dict,
) -> None:
    """The actual human-approval gate for one `write`-access tool call (PRD §6.5/§8.1) -
    called from `SafeHttpApiCall`/`_SafeBuiltinSearchTool` before any real outbound
    request is built. Returns normally only when a human approved the call; raises
    otherwise so the caller can turn that into a `dynamiq.nodes.agents.exceptions.
    ToolExecutionException` (this module stays dynamiq-agnostic, same reasoning
    `safe_http_tool.py` already documents for `SafeHttpClientError`).

    `allow_write_tools` is the calling deployment's own flag (`False` for a deployment
    that disallows write tools entirely, always `True` for Playground/eval - see
    `app.services.runtime`) - checked first, before ever creating a `pending` row, since
    PRD states a write tool "must be allowed by the deployment" as a precondition
    separate from the human-approval decision itself.
    """
    if not allow_write_tools:
        raise WriteToolsNotAllowedError()

    approval_id = await create_pending_approval(company_id, run_id, tool_id, request)
    status = await wait_for_decision(company_id, approval_id)
    if status == "approved":
        return
    if status == "rejected":
        raise ApprovalRejectedError(approval_id)
    raise ApprovalExpiredError(approval_id)
