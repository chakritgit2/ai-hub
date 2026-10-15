"""Resolves a company's own `console.agent_versions` row for Playground (PRD §4.4-B).

Goes through `console.resolve_agent_version` (db/migrations/post/007_resolve_agent_version_function.sql,
SECURITY DEFINER) rather than a `v1_*` view - same reasoning as `app.services.connections`
(v1_* views currently bypass RLS when queried through them). A version belonging to
another company and one that doesn't exist are indistinguishable here (both `None`),
matching PRD §12's "as if it didn't exist" pattern.
"""
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class AgentVersionRow:
    id: str
    spec: dict
    spec_version: str


async def resolve_agent_version(company_id: str, agent_version_id: str) -> AgentVersionRow | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, spec, spec_version "
                "FROM console.resolve_agent_version(:agent_version_id, :company_id)"
            ),
            {"agent_version_id": uuid.UUID(agent_version_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return AgentVersionRow(id=str(row["id"]), spec=row["spec"], spec_version=row["spec_version"])


@dataclass(frozen=True)
class PublishedAgentVersionRow:
    id: str
    agent_id: str
    version_no: int
    is_published: bool
    compiled_definition: dict | None
    compiler_version: str | None
    dynamiq_version: str | None


async def resolve_published_agent_version_for_workflow(
    company_id: str, agent_version_id: str
) -> PublishedAgentVersionRow | None:
    """Resolves a company's own `console.agent_versions` row for the Workflows compiler
    (PRD §6.9). Unlike `resolve_agent_version` above, this returns the row even when
    unpublished - see `db/migrations/post/022_resolve_published_agent_version_for_workflow.sql`
    for why: the caller needs to distinguish "no agent version with this id for this
    company" from "this agent version exists but isn't published yet", two different
    actionable compile errors."""
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, agent_id, version_no, is_published, compiled_definition, "
                "compiler_version, dynamiq_version "
                "FROM console.resolve_published_agent_version_for_workflow(:agent_version_id, :company_id)"
            ),
            {"agent_version_id": uuid.UUID(agent_version_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return PublishedAgentVersionRow(
        id=str(row["id"]),
        agent_id=str(row["agent_id"]),
        version_no=row["version_no"],
        is_published=row["is_published"],
        compiled_definition=row["compiled_definition"],
        compiler_version=row["compiler_version"],
        dynamiq_version=row["dynamiq_version"],
    )
