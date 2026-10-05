"""Resolves a company's own `console.egress_allowlist` rows (PRD §7.4) for SafeHttpClient.

Goes through `console.resolve_egress_allowlist`
(db/migrations/post/010_resolve_egress_allowlist_function.sql, SECURITY DEFINER) — same
reasoning as `app.services.connections.resolve_connection`: a `v1_*` view would be evaluated
as the view owner, not the querying role, so RLS can't be relied on through one.
"""
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class EgressAllowlistEntry:
    host_pattern: str
    port: int | None
    allow_private_ip: bool


async def list_egress_allowlist(company_id: str) -> list[EgressAllowlistEntry]:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT host_pattern, port, allow_private_ip "
                "FROM console.resolve_egress_allowlist(:company_id)"
            ),
            {"company_id": uuid.UUID(company_id)},
        )
        rows = result.mappings().all()

    return [
        EgressAllowlistEntry(
            host_pattern=row["host_pattern"],
            port=row["port"],
            allow_private_ip=row["allow_private_ip"],
        )
        for row in rows
    ]
