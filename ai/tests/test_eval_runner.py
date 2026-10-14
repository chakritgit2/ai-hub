import uuid

import httpx
import pytest

from app.main_runtime import app as runtime_app
from app.main_runtime import require_internal_auth
from app.services.datasets import list_dataset_items, resolve_dataset
from app.services.eval_runner import _tokens_for_item, estimate_eval_cost
from app.services.model_pricing import get_model_pricing

from .markers import fake_internal_claims, requires_postgres

pytestmark = [requires_postgres]


async def test_resolve_dataset_returns_none_for_missing(company_ids):
    company_id = company_ids()
    assert await resolve_dataset(company_id, str(uuid.uuid4())) is None


async def test_resolve_dataset_returns_none_for_wrong_company(company_ids, make_dataset):
    owner_company_id = company_ids()
    other_company_id = company_ids()
    dataset_id = make_dataset(owner_company_id)

    assert await resolve_dataset(other_company_id, dataset_id) is None
    assert (await resolve_dataset(owner_company_id, dataset_id)).name == "test-dataset"


async def test_list_dataset_items_returns_real_items(company_ids, make_dataset, make_dataset_item):
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id, question="Q1", tags=["a", "b"])
    make_dataset_item(company_id, dataset_id, question="Q2", expected_answer=None, context="some context")

    items = await list_dataset_items(company_id, dataset_id)

    assert {item.question for item in items} == {"Q1", "Q2"}
    q1 = next(item for item in items if item.question == "Q1")
    assert q1.tags == ["a", "b"]
    q2 = next(item for item in items if item.question == "Q2")
    assert q2.expected_answer is None
    assert q2.context == "some context"


async def test_list_dataset_items_empty_for_wrong_company(company_ids, make_dataset, make_dataset_item):
    owner_company_id = company_ids()
    other_company_id = company_ids()
    dataset_id = make_dataset(owner_company_id)
    make_dataset_item(owner_company_id, dataset_id)

    assert await list_dataset_items(other_company_id, dataset_id) == []


async def test_get_model_pricing_known_model(company_ids):
    pricing = await get_model_pricing(company_ids(), "gpt-4o-mini")
    assert pricing is not None
    assert pricing.provider == "openai"
    assert pricing.input_per_1k > 0
    assert pricing.output_per_1k > 0


async def test_get_model_pricing_unknown_model(company_ids):
    assert await get_model_pricing(company_ids(), "definitely-not-a-real-model-xyz") is None


async def test_estimate_eval_cost_raises_lookup_error_for_missing_dataset(company_ids, resolvable_agent_version):
    company_id = company_ids()
    agent_version_id = await resolvable_agent_version(company_id)

    with pytest.raises(LookupError):
        await estimate_eval_cost(
            company_id, agent_version_id, str(uuid.uuid4()), ["bleu"], "gpt-4o-mini", "developer"
        )


async def test_estimate_eval_cost_raises_lookup_error_for_missing_agent_version(
    company_ids, make_dataset, make_dataset_item
):
    """A missing agent_version_id must raise LookupError (404), not ValueError (422) -
    the two were indistinguishable before resolve_agent_version was checked directly
    (see estimate_eval_cost's own docstring)."""
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)

    with pytest.raises(LookupError):
        await estimate_eval_cost(
            company_id, str(uuid.uuid4()), dataset_id, ["bleu"], "gpt-4o-mini", "developer"
        )


async def test_estimate_eval_cost_raises_value_error_for_uncompilable_agent_version(
    company_ids, make_dataset, make_dataset_item, make_connection, make_agent_version
):
    """An agent_version_id that resolves but whose spec references a connection that
    doesn't exist must raise ValueError (422), distinct from the LookupError (404) case
    above."""
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    # A real connection row, deliberately never given a stored secret or even referenced -
    # make_agent_version is handed a dangling id instead, so compile_spec's own
    # connection-resolution step fails (app.services.compiler._failure path).
    agent_version_id = make_agent_version(company_id, connection_id=str(uuid.uuid4()))

    with pytest.raises(ValueError):
        await estimate_eval_cost(
            company_id, agent_version_id, dataset_id, ["bleu"], "gpt-4o-mini", "developer"
        )


def test_tokens_for_item_treats_empty_string_answer_as_zero_length():
    """An explicit empty-string expected_answer/context is a real zero-length value,
    distinct from one that was never provided (None) - must not fall back to the
    "no answer measured" default."""
    with_empty_answer = _tokens_for_item("What is 2+2?", "", None, judge_calls=0)
    with_missing_answer = _tokens_for_item("What is 2+2?", None, None, judge_calls=0)

    assert with_empty_answer.agent_output == 0
    assert with_missing_answer.agent_output == 150  # _DEFAULT_ANSWER_TOKENS

    with_empty_context = _tokens_for_item("Q", "A", "", judge_calls=1)
    with_missing_context = _tokens_for_item("Q", "A", None, judge_calls=1)
    assert with_empty_context.judge_input == with_missing_context.judge_input


async def test_estimate_eval_cost_happy_path(
    company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id, question="What is 2+2?", expected_answer="4")
    make_dataset_item(company_id, dataset_id, question="What is the capital of Thailand?", expected_answer="Bangkok")
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    result = await estimate_eval_cost(
        company_id, agent_version_id, dataset_id, ["faithfulness", "bleu"], "gpt-4o-mini", "developer"
    )

    assert result["item_count"] == 2
    assert result["agent_model"] == "gpt-4o-mini"
    assert result["judge_model"] == "gpt-4o-mini"
    assert {m["metric"]: m["llm_calls_per_item"] for m in result["metrics"]} == {"faithfulness": 2, "bleu": 0}
    assert result["estimated_tokens"]["agent_input"] > 0
    assert result["estimated_tokens"]["agent_output"] > 0
    # faithfulness makes 2 judge calls/item * 2 items = 4 calls, bleu makes none
    assert result["estimated_tokens"]["judge_input"] > 0
    assert result["estimated_tokens"]["judge_output"] > 0
    assert result["estimated_cost_usd"]["agent"] is not None and result["estimated_cost_usd"]["agent"] > 0
    assert result["estimated_cost_usd"]["judge"] is not None and result["estimated_cost_usd"]["judge"] > 0
    assert result["estimated_cost_usd"]["total"] == pytest.approx(
        result["estimated_cost_usd"]["agent"] + result["estimated_cost_usd"]["judge"]
    )
    assert result["warnings"] == []


async def test_estimate_eval_cost_no_llm_metrics_has_zero_judge_cost(
    company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    result = await estimate_eval_cost(
        company_id, agent_version_id, dataset_id, ["bleu", "exact_match"], "", "developer"
    )

    assert result["estimated_tokens"]["judge_input"] == 0
    assert result["estimated_tokens"]["judge_output"] == 0
    assert result["estimated_cost_usd"]["judge"] == 0.0
    assert result["warnings"] == []


async def test_estimate_eval_cost_unknown_metric_warns_and_is_excluded(
    company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    result = await estimate_eval_cost(
        company_id, agent_version_id, dataset_id, ["not_a_real_metric"], "gpt-4o-mini", "developer"
    )

    assert result["metrics"] == []
    assert any("not_a_real_metric" in warning for warning in result["warnings"])


async def test_estimate_eval_cost_unknown_pricing_warns_but_still_returns(
    company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="some-unpriced-model")

    result = await estimate_eval_cost(
        company_id, agent_version_id, dataset_id, ["llm_evaluator"], "another-unpriced-model", "developer"
    )

    assert result["estimated_cost_usd"]["agent"] is None
    assert result["estimated_cost_usd"]["judge"] is None
    assert result["estimated_cost_usd"]["total"] is None
    assert any("some-unpriced-model" in warning for warning in result["warnings"])
    assert any("another-unpriced-model" in warning for warning in result["warnings"])


async def test_estimate_eval_cost_total_is_none_when_only_one_side_priced(
    company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    """Previously `(agent_cost or 0) + (judge_cost or 0)` silently treated an unknown
    side as free, presenting a confidently-wrong (too low) total instead of signaling
    that the estimate is partial."""
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")  # priced

    result = await estimate_eval_cost(
        company_id, agent_version_id, dataset_id, ["llm_evaluator"], "some-unpriced-judge-model-xyz", "developer"
    )

    assert result["estimated_cost_usd"]["agent"] is not None
    assert result["estimated_cost_usd"]["judge"] is None
    assert result["estimated_cost_usd"]["total"] is None


@pytest.fixture
async def internal_client(company_ids):
    """Mirrors test_tool_secret_endpoints.py's fixture of the same name."""
    company_id = company_ids()
    runtime_app.dependency_overrides[require_internal_auth] = fake_internal_claims(company_id)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            yield client, company_id
    finally:
        runtime_app.dependency_overrides.pop(require_internal_auth, None)


async def test_estimate_eval_cost_endpoint_happy_path(
    internal_client, make_dataset, make_dataset_item, resolvable_agent_version
):
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    resp = await client.post(
        "/internal/v1/evals/estimate",
        json={
            "agent_version_id": agent_version_id,
            "dataset_id": dataset_id,
            "metrics": ["bleu"],
            "judge_model": "gpt-4o-mini",
            "role": "developer",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["item_count"] == 1
    assert body["agent_model"] == "gpt-4o-mini"


async def test_estimate_eval_cost_endpoint_malformed_id_is_404(internal_client):
    client, _company_id = internal_client
    resp = await client.post(
        "/internal/v1/evals/estimate",
        json={
            "agent_version_id": "not-a-uuid",
            "dataset_id": str(uuid.uuid4()),
            "metrics": [],
            "judge_model": "",
            "role": "developer",
        },
    )
    assert resp.status_code == 404


async def test_estimate_eval_cost_endpoint_missing_dataset_is_404(internal_client, resolvable_agent_version):
    client, company_id = internal_client
    agent_version_id = await resolvable_agent_version(company_id)

    resp = await client.post(
        "/internal/v1/evals/estimate",
        json={
            "agent_version_id": agent_version_id,
            "dataset_id": str(uuid.uuid4()),
            "metrics": [],
            "judge_model": "",
            "role": "developer",
        },
    )
    assert resp.status_code == 404


async def test_estimate_eval_cost_endpoint_missing_agent_version_is_404(
    internal_client, make_dataset, make_dataset_item
):
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)

    resp = await client.post(
        "/internal/v1/evals/estimate",
        json={
            "agent_version_id": str(uuid.uuid4()),
            "dataset_id": dataset_id,
            "metrics": [],
            "judge_model": "",
            "role": "developer",
        },
    )
    assert resp.status_code == 404


async def test_estimate_eval_cost_endpoint_uncompilable_agent_version_is_422(
    internal_client, make_dataset, make_dataset_item, make_agent_version
):
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = make_agent_version(company_id, connection_id=str(uuid.uuid4()))

    resp = await client.post(
        "/internal/v1/evals/estimate",
        json={
            "agent_version_id": agent_version_id,
            "dataset_id": dataset_id,
            "metrics": [],
            "judge_model": "",
            "role": "developer",
        },
    )
    assert resp.status_code == 422
