"""Resolves a company's own published skills (PRD §6.6a) for `ConsoleSkillRegistry`.

Goes through `console.resolve_published_skills`
(db/migrations/post/014_resolve_published_skills_function.sql, SECURITY DEFINER) — same
reasoning as `app.services.tools.resolve_tool`: a `v1_*` view would be evaluated as the view
owner, not the querying role, so RLS can't be relied on through one.
"""
import uuid

import sqlalchemy as sa
from pydantic import BaseModel

from app.core.db import get_company_session


class PublishedSkill(BaseModel):
    name: str
    description: str | None
    content: str


async def list_published_skills(company_id: str) -> list[PublishedSkill]:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT name, description, content FROM console.resolve_published_skills(:company_id)"
            ),
            {"company_id": uuid.UUID(company_id)},
        )
        rows = result.mappings().all()

    return [PublishedSkill(name=row["name"], description=row["description"], content=row["content"]) for row in rows]
