"""Per-company $/request pricing for the 3 Azure-backed guardrail detectors
(PRD §6.4-adjacent, see `app.services.guardrails`'s own module docstring for why this
exists and isn't automatic the way litellm gives real LLM cost).

Reads `console.v1_guardrail_detector_pricing`, not `console.guardrail_detector_pricing`
directly - every cross-service `console.*` read from `ai/` goes through a versioned
`v1_*` view with `security_invoker = true` (see `db/migrations/post/001_create_v1_views.
sql`'s own comment: without it, RLS would evaluate as the view owner, not this service's
own `ai_app`/`gateway_app` role, silently leaking every company's pricing rows).
"""
from dataclasses import dataclass

import sqlalchemy as sa

from app.core.db import get_company_session


@dataclass(frozen=True)
class GuardrailDetectorPricing:
    prices_per_request_usd: dict[str, float]

    def cost_for(self, *azure_call_counts: dict[str, int]) -> float:
        """Sums `count x this company's own configured price` across however many
        `GuardrailOutcome.azure_call_counts` dicts are passed in (e.g. one for the input
        check, one for the output check) - takes the raw dicts rather than full
        `GuardrailOutcome` objects since not every call site has one (the live-streaming
        masking relay path never produces an output-stage `GuardrailOutcome` at all - see
        `app.services.runtime._guardrail_cost_usd`'s own docstring). A check_type this
        company has never priced contributes 0.0, never a guessed number."""
        total = 0.0
        for counts in azure_call_counts:
            for check_type, count in counts.items():
                total += count * self.prices_per_request_usd.get(check_type, 0.0)
        return total


async def get_guardrail_detector_pricing(company_id: str) -> GuardrailDetectorPricing:
    async with get_company_session(company_id) as session:
        rows = await session.execute(
            sa.text(
                "SELECT check_type, price_per_request_usd FROM console.v1_guardrail_detector_pricing"
            )
        )
        return GuardrailDetectorPricing(
            prices_per_request_usd={row.check_type: float(row.price_per_request_usd) for row in rows}
        )
