"""Resolves a company's own `console.agent_versions` row for Playground (PRD §4.4-B).

Goes through `console.resolve_agent_version` (db/migrations/post/007_resolve_agent_version_function.sql,
SECURITY DEFINER) rather than a `v1_*` view - same reasoning as `app.services.connections`
(v1_* views currently bypass RLS when queried through them). A version belonging to
another company and one that doesn't exist are indistinguishable here (both `None`),
matching PRD §12's "as if it didn't exist" pattern.
"""
import json
import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa

from app.core.db import get_company_session, get_sessionmaker
from app.services.compiler import compile_spec


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


@dataclass(frozen=True)
class RecompileFailure:
    agent_version_id: str
    error: str


@dataclass(frozen=True)
class RecompileAllResult:
    recompiled: int = 0
    failed: list[RecompileFailure] = field(default_factory=list)


async def recompile_all_published_agent_versions() -> RecompileAllResult:
    """Re-runs the compiler against every company's published agent versions (PRD §12:
    "upgrade the compiler and run recompile-all on staging first - specs stay unchanged").

    Reads/writes through `console.list_published_agent_versions_for_recompile` /
    `console.update_recompiled_agent_version` (db/migrations/post/034_recompile_all_functions.sql)
    rather than RLS, same reasoning as `resolve_agent_version` above - this crosses every
    company's RLS boundary at once, which no per-request session is scoped for.

    Each version is compiled with role="admin" (the full current node-type allowlist, not
    whatever role happened to publish it) since this validates against the compiler/allowlist
    as it stands today, not any one user's permissions. A version that compiles successfully
    has its `compiled_definition`/`compiler_version`/`dynamiq_version` overwritten in place -
    the retroactive rewrite PRD §12 describes, which is why this is meant to run on staging
    before a compiler upgrade ships to production. A version that fails to compile is left
    untouched (its currently-deployed compiled_definition keeps working) and reported back
    instead, same as one unpublished between the list read and the write (not an error -
    just excluded from both `recompiled` and `failed`).
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, company_id, spec "
                    "FROM console.list_published_agent_versions_for_recompile()"
                )
            )
        ).mappings().all()

    recompiled = 0
    failed: list[RecompileFailure] = []
    for row in rows:
        agent_version_id = str(row["id"])
        try:
            result = await compile_spec(row["spec"], "admin", str(row["company_id"]))
        except Exception as exc:  # one bad version must not abort the whole batch
            failed.append(RecompileFailure(agent_version_id=agent_version_id, error=str(exc)))
            continue

        if not result["ok"]:
            message = "; ".join(f"{e['path']}: {e['message']}" for e in result["errors"])
            failed.append(RecompileFailure(agent_version_id=agent_version_id, error=message))
            continue

        async with sessionmaker() as session, session.begin():
            updated = (
                await session.execute(
                    sa.text(
                        "SELECT id FROM console.update_recompiled_agent_version("
                        ":id, :compiled_definition::jsonb, :compiler_version, :dynamiq_version)"
                    ),
                    {
                        "id": agent_version_id,
                        "compiled_definition": json.dumps(result["compiled_definition"]),
                        "compiler_version": result["compiler_version"],
                        "dynamiq_version": result["dynamiq_version"],
                    },
                )
            ).scalar_one_or_none()
        if updated is not None:
            recompiled += 1

    return RecompileAllResult(recompiled=recompiled, failed=failed)
