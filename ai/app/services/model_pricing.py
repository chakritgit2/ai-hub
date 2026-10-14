"""Read-only lookup against `console.model_pricing` (PRD §8.2) - a genuinely
platform-wide reference table, unlike every other `console.*` table ai-runtime reads: no
`company_id` column, no RLS, no resolver function needed (see
db/migrations/post/018_model_pricing_grants.sql's comment for why a plain grant is
correct here). Still goes through `get_company_session` for the one connection-pooled
session entry point this codebase uses everywhere - the company_id it sets just happens
to be irrelevant to this particular query.
"""
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class ModelPricingRow:
    provider: str
    model: str
    input_per_1k: float
    output_per_1k: float


async def get_model_pricing(company_id: str, model: str) -> ModelPricingRow | None:
    """`None` when `model` isn't in the table - callers must treat that as "unknown
    pricing", not an error (PRD §6.7's cost estimate is a best-effort preview, not a
    hard requirement to run an eval). Looked up by `model` alone (the only identifier
    callers actually have - a compiled agent's `llm.model`/a chosen judge_model is just a
    bare model name, never tagged with its provider) even though the table's real unique
    key is `(provider, model)` - two providers reusing the same model name would be
    ambiguous, so this takes whichever sorts first rather than erroring, since an
    approximate estimate from the "wrong" provider's pricing is still more useful than
    none."""
    async with get_company_session(company_id) as session:
        result = await session.execute(
            sa.text(
                "SELECT provider, model, input_per_1k, output_per_1k "
                "FROM console.model_pricing WHERE model = :model ORDER BY provider LIMIT 1"
            ),
            {"model": model},
        )
        row = result.mappings().first()

    if row is None:
        return None

    return ModelPricingRow(
        provider=row["provider"],
        model=row["model"],
        input_per_1k=float(row["input_per_1k"]),
        output_per_1k=float(row["output_per_1k"]),
    )
