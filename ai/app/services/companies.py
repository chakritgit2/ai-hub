"""Company status (PRD §7.3/§7.7).

`console.v1_companies` is schema `console`, but granted straight to `ai_app`/`gateway_app`
(db/migrations/post/001_create_v1_views.sql) - no `SECURITY DEFINER` function needed, this
reads it directly the same way `app.services.quotas`/`company_budget` already do for other
columns on the same view.
"""
import uuid

import sqlalchemy as sa

from app.core.db import get_company_session


async def get_company_status(company_id: str) -> str | None:
    """`None` if the company id doesn't resolve at all - callers should treat that the same
    as a suspended company (deny), not a different, unhandled case."""
    async with get_company_session(company_id) as session:
        row = (
            await session.execute(
                sa.text("SELECT status FROM console.v1_companies WHERE id = :company_id"),
                {"company_id": uuid.UUID(company_id)},
            )
        ).mappings().one_or_none()
    return None if row is None else row["status"]
