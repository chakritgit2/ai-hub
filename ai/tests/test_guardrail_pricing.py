from app.services.guardrail_pricing import GuardrailDetectorPricing


def test_cost_for_sums_count_times_price_per_check_type():
    pricing = GuardrailDetectorPricing(
        prices_per_request_usd={"prompt_injection": 0.001, "content_moderation": 0.0015}
    )

    cost = pricing.cost_for({"prompt_injection": 2, "content_moderation": 1})

    assert cost == 0.001 * 2 + 0.0015 * 1


def test_cost_for_combines_multiple_count_dicts():
    """app.services.runtime calls this once with input's counts and once with output's -
    they must add together, not overwrite each other."""
    pricing = GuardrailDetectorPricing(prices_per_request_usd={"pii_model": 0.0008})

    cost = pricing.cost_for({"pii_model": 1}, {"pii_model": 1})

    assert cost == 0.0008 * 2


def test_cost_for_is_zero_for_an_unpriced_check_type():
    """A company that never configured a price for a check_type pays nothing for it -
    never a guessed number."""
    pricing = GuardrailDetectorPricing(prices_per_request_usd={})

    cost = pricing.cost_for({"prompt_injection": 5})

    assert cost == 0.0


def test_cost_for_with_no_calls_at_all_is_zero():
    pricing = GuardrailDetectorPricing(prices_per_request_usd={"prompt_injection": 0.001})

    assert pricing.cost_for({}) == 0.0
    assert pricing.cost_for() == 0.0
