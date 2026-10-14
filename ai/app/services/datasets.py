"""Read-only access to console.datasets/console.dataset_items for ai-runtime (PRD §6.7).

Datasets themselves are pure console-api/Phinx-owned CRUD (no ai-runtime involvement at
all) - this module exists only so eval cost estimation/execution can read a company's own
dataset items, through `console.resolve_dataset`/`resolve_dataset_items`
(db/migrations/post/019_resolve_dataset_functions.sql, SECURITY DEFINER) rather than a
`v1_*` view, same reasoning as `app.services.connections`/`knowledge_bases`. A dataset
belonging to another company and one that doesn't exist are indistinguishable here (both
`None`/empty), matching PRD §12's "as if it didn't exist" pattern.
"""
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class DatasetRow:
    id: str
    name: str


@dataclass(frozen=True)
class DatasetItemRow:
    id: str
    question: str
    expected_answer: str | None
    context: str | None
    tags: list[str]


async def resolve_dataset(company_id: str, dataset_id: str) -> DatasetRow | None:
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text("SELECT id, name FROM console.resolve_dataset(:dataset_id, :company_id)"),
            {"dataset_id": uuid.UUID(dataset_id), "company_id": uuid.UUID(company_id)},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None

    return DatasetRow(id=str(row["id"]), name=row["name"])


async def list_dataset_items(company_id: str, dataset_id: str) -> list[DatasetItemRow]:
    """Empty list for a dataset that doesn't exist/belongs to another company - callers
    that need to distinguish "no items" from "no such dataset" should call
    `resolve_dataset` first, same as every other resolve-then-list pair in this codebase."""
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT id, question, expected_answer, context, tags "
                "FROM console.resolve_dataset_items(:dataset_id, :company_id)"
            ),
            {"dataset_id": uuid.UUID(dataset_id), "company_id": uuid.UUID(company_id)},
        )
        rows = result.mappings().all()

    return [
        DatasetItemRow(
            id=str(row["id"]),
            question=row["question"],
            expected_answer=row["expected_answer"],
            context=row["context"],
            tags=row["tags"] or [],
        )
        for row in rows
    ]
