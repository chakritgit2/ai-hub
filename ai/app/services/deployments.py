"""Resolves a company's own `console.deployments` row for the gateway (PRD §6.3).

Queries `console.deployments` directly, scoped by `get_company_session`'s `SET LOCAL
app.company_id` + the table's own RLS policy - unlike `console.connections`/
`console.agent_versions`, no SECURITY DEFINER function was needed here: verified live
that `ai_app` can read this table and that RLS correctly isolates by company_id (a
cross-company id resolves to `None`, same as a nonexistent one)."""
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class DeploymentRow:
    id: str
    agent_version_id: str
    rate_limit_per_min: int
    daily_token_limit: int | None
    daily_cost_limit_usd: float | None
    allowed_origins: list[str]
    guardrail_overrides: dict
    output_mode: str
    allow_write_tools: bool
    conversation_ttl_days: int
    enabled: bool


async def resolve_deployment_id_by_slug(company_id: str, slug: str) -> str | None:
    """Maps a URL slug to its deployment id, scoped to `company_id` - used only to
    cross-check a session token's embedded `deployment_id` claim against the slug the
    browser actually called (PRD §7.5), never to authenticate on its own (the token
    itself, verified separately, is what proves `company_id`)."""
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text("SELECT id FROM console.deployments WHERE slug = :slug AND company_id = :company_id"),
            {"slug": slug, "company_id": uuid.UUID(company_id)},
        )
        row = result.one_or_none()

    return str(row[0]) if row is not None else None


async def resolve_deployment(company_id: str, deployment_id: str) -> DeploymentRow | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, agent_version_id, rate_limit_per_min, daily_token_limit, daily_cost_limit_usd, "
                "allowed_origins, guardrail_overrides, output_mode, allow_write_tools, "
                "conversation_ttl_days, enabled "
                "FROM console.deployments WHERE id = :id AND company_id = :company_id"
            ),
            {"id": uuid.UUID(deployment_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return DeploymentRow(
        id=str(row["id"]),
        agent_version_id=str(row["agent_version_id"]),
        rate_limit_per_min=row["rate_limit_per_min"],
        daily_token_limit=row["daily_token_limit"],
        daily_cost_limit_usd=float(row["daily_cost_limit_usd"]) if row["daily_cost_limit_usd"] is not None else None,
        allowed_origins=row["allowed_origins"],
        guardrail_overrides=row["guardrail_overrides"],
        output_mode=row["output_mode"],
        allow_write_tools=row["allow_write_tools"],
        conversation_ttl_days=row["conversation_ttl_days"],
        enabled=row["enabled"],
    )
