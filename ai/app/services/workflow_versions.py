"""Resolves a company's own `console.workflow_versions` row for Playground (PRD §6.9,
phase 4, slice 2).

Goes through `console.resolve_workflow_version` (db/migrations/post/023_resolve_workflow_version_function.sql,
SECURITY DEFINER) rather than a `v1_*` view - same reasoning as `app.services.agent_versions`
(v1_* views currently bypass RLS when queried through them). A version belonging to
another company and one that doesn't exist are indistinguishable here (both `None`),
matching PRD §12's "as if it didn't exist" pattern.
"""
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class WorkflowVersionRow:
    id: str
    spec: dict
    spec_version: str


@dataclass(frozen=True)
class WorkflowIdentityRow:
    name: str


async def resolve_workflow_version(company_id: str, workflow_version_id: str) -> WorkflowVersionRow | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, spec, spec_version "
                "FROM console.resolve_workflow_version(:workflow_version_id, :company_id)"
            ),
            {"workflow_version_id": uuid.UUID(workflow_version_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return WorkflowVersionRow(id=str(row["id"]), spec=row["spec"], spec_version=row["spec_version"])


async def resolve_workflow_identity(company_id: str, workflow_version_id: str) -> WorkflowIdentityRow | None:
    """Used only by the public gateway's `GET /v1/deployments/{slug}/info` for a
    workflow-backed deployment - a workflow has no identity-shaped spec field the way an
    agent does, so the closest equivalent is just its own `name`
    (db/migrations/post/024_resolve_workflow_identity_for_gateway.sql, SECURITY DEFINER,
    never exposes `spec`/`compiled_definition`)."""
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT name FROM console.resolve_workflow_identity(:workflow_version_id, :company_id)"
            ),
            {"workflow_version_id": uuid.UUID(workflow_version_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return WorkflowIdentityRow(name=row["name"])
