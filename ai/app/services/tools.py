"""Resolves a company's own `console.tools` row (PRD §6.5) for the test endpoint.

Goes through `console.resolve_tool`
(db/migrations/post/012_resolve_tool_function.sql, SECURITY DEFINER) — same reasoning as
`app.services.connections.resolve_connection`: a `v1_*` view would be evaluated as the view
owner, not the querying role, so RLS can't be relied on through one. A tool belonging to
another company and one that doesn't exist are indistinguishable here (both `None`),
matching PRD §12's "as if it didn't exist" pattern.
"""
import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class ToolRow:
    id: str
    name: str
    kind: str
    access_level: str
    auth_mode: str
    audience: str | None
    config: dict[str, Any]
    enabled: bool


async def resolve_tool(company_id: str, tool_id: str) -> ToolRow | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, name, kind, access_level, auth_mode, audience, config, enabled "
                "FROM console.resolve_tool(:tool_id, :company_id)"
            ),
            {"tool_id": uuid.UUID(tool_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return ToolRow(
        id=str(row["id"]),
        name=row["name"],
        kind=row["kind"],
        access_level=row["access_level"],
        auth_mode=row["auth_mode"],
        audience=row["audience"],
        config=row["config"],
        enabled=row["enabled"],
    )
