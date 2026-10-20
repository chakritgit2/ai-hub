"""Per-company monthly spend budget (PRD phase 3: "per-company monthly budget").

Unlike `quotas.py`'s per-deployment daily limits (Redis counters, reserve-then-reconcile,
sized for a sub-second decision on every single run), this reads the company's real
`logs.runs.cost_usd` total for the current Bangkok month directly from Postgres - a
monthly window sees far less request volume per company than a daily quota, so a live
aggregate query (the same one `DashboardController::getDashboardSummary`'s `cost_month`
figure already runs from the console-api side) is simpler and provably consistent with
what the Settings/Dashboard pages show, with no second counter to keep in sync with the
DB's own billing source of truth or reset at a month boundary.

Scoped to gateway/deployment calls only (`run_deployment_agent`/`run_deployment_workflow`)
- this is a governance control over the company's customer-facing API usage, the same
place `daily_cost_limit_usd` already applies, not every LLM call a company's own
developers make from the Playground or an Evaluation run while building/testing.

Weaker than `quotas.py`'s reserve-before-run daily design: this is a read-only precheck,
same accepted race `check_quota_not_exceeded` already documents for workflow deployments
- concurrent requests landing right at the boundary can jointly push spend over budget by
one in-flight batch before the next request's check sees the new total. Acceptable for a
monthly, human-scale budget control, not a hard real-time spending cap.
"""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from app.core.db import get_company_session
from app.db.tables import runs_table

_BANGKOK = ZoneInfo("Asia/Bangkok")


@dataclass(frozen=True)
class MonthlyBudgetCheck:
    allowed: bool
    reason: str | None = None  # "monthly_budget" when allowed=False
    reset_at: datetime | None = None  # start of next Bangkok month, when allowed=False


def _bangkok_month_bounds() -> tuple[datetime, datetime]:
    month_start = datetime.now(_BANGKOK).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = (
        month_start.replace(year=month_start.year + 1, month=1)
        if month_start.month == 12
        else month_start.replace(month=month_start.month + 1)
    )
    return month_start, next_month_start


async def check_monthly_budget_not_exceeded(company_id: str) -> MonthlyBudgetCheck:
    """`console.companies.monthly_budget_usd` unset (None) means no monthly cap is
    configured - allowed unconditionally, same "optional limit" convention every other
    quota field in this codebase already follows, without even touching `logs.runs`."""
    async with get_company_session(company_id) as session:
        budget_row = await session.execute(
            sa.text("SELECT monthly_budget_usd FROM console.v1_companies WHERE id = :company_id"),
            {"company_id": company_id},
        )
        monthly_budget_usd = budget_row.scalar()
        if monthly_budget_usd is None:
            return MonthlyBudgetCheck(allowed=True)

        month_start, next_month_start = _bangkok_month_bounds()
        spend_row = await session.execute(
            sa.select(sa.func.coalesce(sa.func.sum(runs_table.c.cost_usd), 0)).where(
                runs_table.c.created_at >= month_start
            )
        )
        current_spend_usd = float(spend_row.scalar() or 0)

    if current_spend_usd >= float(monthly_budget_usd):
        return MonthlyBudgetCheck(allowed=False, reason="monthly_budget", reset_at=next_month_start)

    return MonthlyBudgetCheck(allowed=True)
