"""Resolves a company's own `console.connections` row for the compiler (PRD §7.7).

Goes through `console.resolve_connection` (db/migrations/post/004_resolve_connection_function.sql,
SECURITY DEFINER) rather than a `v1_*` view — see that migration's comment for why
(v1_* views currently bypass RLS when queried through them). A connection belonging to
another company and one that doesn't exist are indistinguishable here (both `None`),
matching PRD §12's "as if it didn't exist" pattern.
"""
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class ConnectionRow:
    id: str
    name: str
    type: str
    api_base: str | None
    max_concurrency: int


async def resolve_connection(company_id: str, connection_id: str) -> ConnectionRow | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, name, type, api_base, max_concurrency "
                "FROM console.resolve_connection(:connection_id, :company_id)"
            ),
            {"connection_id": uuid.UUID(connection_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return ConnectionRow(
        id=str(row["id"]),
        name=row["name"],
        type=row["type"],
        api_base=row["api_base"],
        max_concurrency=row["max_concurrency"],
    )
