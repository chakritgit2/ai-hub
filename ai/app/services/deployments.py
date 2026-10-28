"""Resolves a company's own `console.deployments` row for the gateway (PRD §6.3).

Queries `console.deployments` directly, scoped by `get_company_session`'s `SET LOCAL
app.company_id` + the table's own RLS policy - unlike `console.connections`/
`console.agent_versions`, no SECURITY DEFINER function was needed here: verified live
that `ai_app` can read this table and that RLS correctly isolates by company_id (a
cross-company id resolves to `None`, same as a nonexistent one).

`resolve_deployment` also implements the PRD §4.4/§4.5 gateway config cache: console-api
bumps `config_version` on every mutation (update/promote/publish - see
`DeploymentsController.php`/`AgentsController.php`/`WorkflowsController.php`), so a cache
hit only needs a cheap `config_version` comparison, not a full row refetch, and a DB error
during that comparison falls back to the last-known row instead of failing the request
(the "gateway keeps serving from cached config" brief-failover tolerance). This is an
in-process cache (per ai-gateway/ai-runtime pod) - no cross-pod invalidation, bounded
staleness only, same tradeoff the PRD's own "lost notification" sentence accepts."""
import time
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import get_company_session

_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class DeploymentRow:
    id: str
    # Exactly one of these two is set (DB CHECK constraint enforces it) - agent_version_id
    # for an Agent-backed deployment, workflow_version_id for a Workflow-backed one (PRD
    # §6.3/§6.9 phase 4 workflow deployment exposure).
    agent_version_id: str | None
    workflow_version_id: str | None
    rate_limit_per_min: int
    daily_token_limit: int | None
    daily_cost_limit_usd: float | None
    allowed_origins: list[str]
    guardrail_overrides: dict
    output_mode: str
    allow_write_tools: bool
    conversation_ttl_days: int
    enabled: bool


@dataclass
class _CacheEntry:
    row: DeploymentRow
    config_version: int
    last_checked: float


_cache: dict[tuple[str, str], _CacheEntry] = {}


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


async def _fetch_config_version(company_id: str, deployment_id: str) -> int | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text("SELECT config_version FROM console.deployments WHERE id = :id AND company_id = :company_id"),
            {"id": uuid.UUID(deployment_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.one_or_none()

    return row[0] if row is not None else None


async def _fetch_deployment_row(company_id: str, deployment_id: str) -> tuple[DeploymentRow, int] | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, agent_version_id, workflow_version_id, rate_limit_per_min, daily_token_limit, "
                "daily_cost_limit_usd, allowed_origins, guardrail_overrides, output_mode, allow_write_tools, "
                "conversation_ttl_days, enabled, config_version "
                "FROM console.deployments WHERE id = :id AND company_id = :company_id"
            ),
            {"id": uuid.UUID(deployment_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    deployment = DeploymentRow(
        id=str(row["id"]),
        agent_version_id=str(row["agent_version_id"]) if row["agent_version_id"] is not None else None,
        workflow_version_id=str(row["workflow_version_id"]) if row["workflow_version_id"] is not None else None,
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
    return deployment, row["config_version"]


async def resolve_deployment(company_id: str, deployment_id: str) -> DeploymentRow | None:
    key = (company_id, deployment_id)
    now = time.monotonic()
    cached = _cache.get(key)

    if cached is not None and (now - cached.last_checked) < _CACHE_TTL_SECONDS:
        return cached.row

    try:
        current_version = await _fetch_config_version(company_id, deployment_id)
    except SQLAlchemyError:
        if cached is not None:
            # Also push last_checked forward, not just serve the stale row - otherwise a
            # sustained outage (not just a "brief" one) makes every single request retry
            # the DB immediately again instead of backing off for another TTL window,
            # turning the cache into a retry storm against an already-struggling DB.
            cached.last_checked = now
            return cached.row
        raise

    if current_version is None:
        _cache.pop(key, None)
        return None

    if cached is not None and cached.config_version == current_version:
        cached.last_checked = now
        return cached.row

    try:
        fetched = await _fetch_deployment_row(company_id, deployment_id)
    except SQLAlchemyError:
        if cached is not None:
            cached.last_checked = now  # same backoff reasoning as above
            return cached.row
        raise

    if fetched is None:
        _cache.pop(key, None)
        return None

    row, version = fetched
    _cache[key] = _CacheEntry(row=row, config_version=version, last_checked=now)
    return row
