import asyncio
import uuid
from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest
from dynamiq.runnables.base import RunnableStatus

from app.core.db import get_company_session
from app.db.tables import eval_results_table
from app.main_runtime import app as runtime_app
from app.main_runtime import require_internal_auth
from app.services import eval_runner as eval_runner_module
from app.services.agent_spec import GuardrailsSpec
from app.services.datasets import DatasetItemRow, list_dataset_items, resolve_dataset
from app.services.eval_runner import _run_item, _tokens_for_item, estimate_eval_cost, run_eval
from app.services.eval_runs import create_eval_run, get_eval_run
from app.services.model_pricing import get_model_pricing

from .markers import fake_internal_claims, requires_postgres

pytestmark = [requires_postgres]


class _ScoringFakeAgent:
    """Like test_playground_guardrails.py's _RecordingAgent, but keyed by input text so
    different dataset items in the same eval run can be made to succeed or fail
    independently (needed to test per-item failure isolation in run_eval)."""

    calls: ClassVar[list[str]] = []
    replies: ClassVar[dict[str, str]] = {}
    failures: ClassVar[set[str]] = set()

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
        question = input_data["input"]
        type(self).calls.append(question)
        if config is not None:
            for callback in config.callbacks:
                callback.on_node_execute_run(
                    {},
                    usage_data={
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "total_tokens_cost_usd": 0.001,
                    },
                )
        if question in type(self).failures:
            raise RuntimeError(f"boom for {question!r}")
        reply = type(self).replies.get(question, question)
        return SimpleNamespace(output={"content": reply}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def scoring_fake_agent(monkeypatch):
    import dynamiq.nodes.agents

    from app.services import compiler

    _ScoringFakeAgent.calls = []
    _ScoringFakeAgent.replies = {}
    _ScoringFakeAgent.failures = set()
    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _ScoringFakeAgent)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))
    return _ScoringFakeAgent


async def _fetch_eval_results(company_id: str, eval_run_id: str) -> dict[str, dict]:
    async with get_company_session(company_id) as session:
        rows = (
            await session.execute(
                eval_results_table.select().where(eval_results_table.c.eval_run_id == uuid.UUID(eval_run_id))
            )
        ).mappings().all()
    return {str(row["dataset_item_id"]): dict(row) for row in rows}


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


async def test_run_item_disallows_write_tools(monkeypatch) -> None:
    """An eval run is unattended - no human is watching to approve/reject a write-tool
    call - so `_run_item` must pass `allow_write_tools=False` to `execute_agent_run`
    (fails the tool call immediately, same as a deployment that disallows write tools)
    rather than the `True` default Playground/an allowing deployment get, which would
    create a `pending` runtime.tool_approvals row that's guaranteed to sit unapproved
    until it expires after the full 5-minute PRD window."""
    captured: dict = {}

    async def _fake_execute_agent_run(*_args, **kwargs):
        captured["allow_write_tools"] = kwargs.get("allow_write_tools")
        return {"output": "fake answer", "cost_usd": 0.0}

    monkeypatch.setattr(eval_runner_module, "execute_agent_run", _fake_execute_agent_run)

    item = DatasetItemRow(id="item-1", question="Q", expected_answer=None, context=None, tags=[])
    await _run_item(asyncio.Semaphore(1), {}, GuardrailsSpec(), "company-1", "agent-version-1", item)

    assert captured["allow_write_tools"] is False


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


async def test_run_eval_happy_path_non_llm_metrics(
    scoring_fake_agent, company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    item1 = make_dataset_item(company_id, dataset_id, question="Q1", expected_answer="4")
    item2 = make_dataset_item(company_id, dataset_id, question="Q2", expected_answer="Bangkok")
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")
    scoring_fake_agent.replies = {"Q1": "4", "Q2": "Bangkok"}  # exact matches

    eval_run = await create_eval_run(
        company_id, agent_version_id, dataset_id, "developer", ["exact_match", "string_presence"], None, None
    )

    summary = await run_eval(company_id, eval_run.id)

    assert summary["item_count"] == 2
    assert summary["success_count"] == 2
    assert summary["failure_count"] == 0
    assert summary["mean_scores"]["exact_match"] == 1.0
    assert summary["mean_scores"]["string_presence"] == 1.0
    assert summary["skipped_metrics"] == []
    assert summary["metric_errors"] == {}

    updated = await get_eval_run(company_id, eval_run.id)
    assert updated.status == "completed"
    assert updated.summary["mean_scores"]["exact_match"] == 1.0

    results = await _fetch_eval_results(company_id, eval_run.id)
    assert len(results) == 2
    assert results[item1]["scores"]["exact_match"] == 1.0
    assert results[item1]["error"] is None
    assert results[item1]["run_id"] is not None
    assert results[item2]["scores"]["exact_match"] == 1.0


async def test_run_eval_metric_scoring_failure_does_not_abort_the_run(
    scoring_fake_agent, company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    """Found live while testing this feature: dynamiq's BleuScoreEvaluator crashes
    (IndexError deep inside sacrebleu) on a single-word answer with no sentence-ending
    punctuation - its own sentence splitter filters single-word "sentences" out entirely,
    leaving sacrebleu nothing to score. Realistic for eval datasets (short factual
    answers like "4"/"Bangkok" are exactly this shape) - one metric's own bug must not
    fail the whole eval run, same per-item isolation principle applied at the
    per-metric-batch level."""
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id, question="Q1", expected_answer="4")
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")
    scoring_fake_agent.replies = {"Q1": "4"}

    eval_run = await create_eval_run(
        company_id, agent_version_id, dataset_id, "developer", ["exact_match", "bleu"], None, None
    )

    summary = await run_eval(company_id, eval_run.id)

    assert summary["mean_scores"]["exact_match"] == 1.0
    assert "bleu" not in summary["mean_scores"]
    assert "bleu" in summary["metric_errors"]

    updated = await get_eval_run(company_id, eval_run.id)
    assert updated.status == "completed"


async def test_run_eval_rejects_unsupported_metrics_but_still_runs_the_rest(
    scoring_fake_agent, company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    """main_runtime.py's POST /internal/v1/evals is the real rejection point for
    `llm_evaluator`/`python_evaluator` - this exercises run_eval's own defensive
    backstop directly (bypassing that endpoint), proving a stray unsupported metric name
    never reaches a dynamiq evaluator and doesn't block the supported metrics."""
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id, question="Q1", expected_answer="4")
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")
    scoring_fake_agent.replies = {"Q1": "4"}

    eval_run = await create_eval_run(
        company_id, agent_version_id, dataset_id, "developer", ["exact_match", "python_evaluator"], None, None
    )

    summary = await run_eval(company_id, eval_run.id)

    assert summary["mean_scores"]["exact_match"] == 1.0
    assert summary["skipped_metrics"] == ["python_evaluator"]
    assert "python_evaluator" not in summary["mean_scores"]


async def test_run_eval_skips_metric_for_item_missing_required_field(
    scoring_fake_agent, company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    item_with_answer = make_dataset_item(company_id, dataset_id, question="Q1", expected_answer="4")
    item_without_answer = make_dataset_item(company_id, dataset_id, question="Q2", expected_answer=None)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")
    scoring_fake_agent.replies = {"Q1": "4", "Q2": "whatever"}

    eval_run = await create_eval_run(
        company_id, agent_version_id, dataset_id, "developer", ["exact_match"], None, None
    )

    summary = await run_eval(company_id, eval_run.id)

    assert summary["mean_scores"]["exact_match"] == 1.0  # only item_with_answer contributes

    results = await _fetch_eval_results(company_id, eval_run.id)
    assert results[item_with_answer]["scores"] == {"exact_match": 1.0}
    assert results[item_without_answer]["scores"] is None
    assert results[item_without_answer]["error"] is None  # the agent run itself succeeded


async def test_run_eval_per_item_failure_does_not_abort_batch(
    scoring_fake_agent, company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    good_item = make_dataset_item(company_id, dataset_id, question="Q-good", expected_answer="4")
    bad_item = make_dataset_item(company_id, dataset_id, question="Q-bad", expected_answer="Bangkok")
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")
    scoring_fake_agent.replies = {"Q-good": "4"}
    scoring_fake_agent.failures = {"Q-bad"}

    eval_run = await create_eval_run(
        company_id, agent_version_id, dataset_id, "developer", ["exact_match"], None, None
    )

    summary = await run_eval(company_id, eval_run.id)

    assert summary["success_count"] == 1
    assert summary["failure_count"] == 1

    updated = await get_eval_run(company_id, eval_run.id)
    assert updated.status == "completed"  # one bad item must not fail the whole run

    results = await _fetch_eval_results(company_id, eval_run.id)
    assert results[good_item]["error"] is None
    assert results[good_item]["scores"] == {"exact_match": 1.0}
    assert results[bad_item]["error"] is not None
    assert results[bad_item]["scores"] is None


async def test_run_eval_scores_llm_based_metric_via_judge_llm_reusing_agent_connection(
    scoring_fake_agent, monkeypatch, company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    """Proves the judge-LLM wiring: a requested LLM-based metric builds a judge LLM from
    the *agent's own* connection (confirmed design choice - no separate judge connection
    concept in this slice) and records each item's score + reasoning. Fakes
    FaithfulnessEvaluator itself (like test_playground_guardrails.py fakes dynamiq's
    Agent) rather than exercising dynamiq's real prompt/LLM parsing."""
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    item1 = make_dataset_item(company_id, dataset_id, question="Q1", expected_answer="4", context="ctx1")
    item2 = make_dataset_item(company_id, dataset_id, question="Q2", expected_answer="Bangkok", context="ctx2")
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")
    scoring_fake_agent.replies = {"Q1": "ans1", "Q2": "ans2"}

    captured: dict = {}

    class _FakeResult:
        def __init__(self, score: float, reasoning: str) -> None:
            self.score = score
            self.reasoning = reasoning

    class _FakeOutput:
        def __init__(self, results: list[_FakeResult]) -> None:
            self.results = results

    class _FakeFaithfulnessEvaluator:
        def __init__(self, llm) -> None:
            captured["llm"] = llm

        def run(self, questions, answers, contexts):
            captured["questions"] = questions
            captured["answers"] = answers
            captured["contexts"] = contexts
            return _FakeOutput([_FakeResult(0.9, "good"), _FakeResult(0.5, "meh")])

    monkeypatch.setattr(eval_runner_module, "FaithfulnessEvaluator", _FakeFaithfulnessEvaluator)
    monkeypatch.setattr(eval_runner_module, "build_llm", lambda *_args, **_kwargs: (None, "fake-judge-llm"))

    eval_run = await create_eval_run(
        company_id, agent_version_id, dataset_id, "developer", ["faithfulness"], "gpt-4o-mini", None
    )

    summary = await run_eval(company_id, eval_run.id)

    assert captured["llm"] == "fake-judge-llm"
    assert len(captured["questions"]) == 2
    assert summary["mean_scores"]["faithfulness"] == pytest.approx(0.7)

    results = await _fetch_eval_results(company_id, eval_run.id)
    assert results[item1]["reasons"]["faithfulness"] in ("good", "meh")
    assert results[item2]["reasons"]["faithfulness"] in ("good", "meh")


async def test_run_eval_fails_closed_when_llm_metric_requested_without_judge_model(
    scoring_fake_agent, company_ids, make_dataset, make_dataset_item, resolvable_agent_version
):
    """Defensive backstop (found via code-review): the real rejection is
    main_runtime.py's createEvalRun (422 judge_model_required) before an eval_runs row
    ever exists - this exercises run_eval's own `_build_judge_llm` guard directly, by
    constructing the row with a falsy judge_model the same way a bug in that upstream
    check could let through. Must fail the run cleanly (status="failed"), never build a
    judge LLM with model=None."""
    company_id = company_ids()
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id, question="Q1", expected_answer="4", context="ctx1")
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    eval_run = await create_eval_run(
        company_id, agent_version_id, dataset_id, "developer", ["faithfulness"], None, None
    )

    summary = await run_eval(company_id, eval_run.id)

    assert summary["status"] == "failed"
    assert "judge_model" in summary["error"]

    updated = await get_eval_run(company_id, eval_run.id)
    assert updated.status == "failed"


async def test_run_eval_missing_eval_run_marks_status_failed(company_ids):
    """run_eval never crashes the ARQ job loop - an eval_run_id that doesn't resolve
    (e.g. deleted between enqueue and the job actually running) must still return
    gracefully, same discipline kb_indexer.py::index_kb_document already established."""
    company_id = company_ids()

    result = await run_eval(company_id, str(uuid.uuid4()))

    assert result["status"] == "failed"
    assert "not found" in result["error"]
