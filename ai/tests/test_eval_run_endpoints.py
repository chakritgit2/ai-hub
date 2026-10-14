import uuid

import httpx
import pytest

from app import main_runtime as main_runtime_module
from app.main_runtime import app as runtime_app
from app.main_runtime import require_internal_auth

from .markers import fake_internal_claims, requires_postgres

pytestmark = [requires_postgres]


class _FakeArqPool:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple]] = []

    async def enqueue_job(self, name: str, *args) -> None:
        self.enqueued.append((name, args))


@pytest.fixture
def fake_arq_pool(monkeypatch):
    """Stands in for `app.core.redis.get_arq_pool` so `POST /internal/v1/evals` never
    touches a real Redis - mirrors how `scoring_fake_agent`/`recording_agent` stand in
    for the real Dynamiq Agent elsewhere in this test suite."""
    pool = _FakeArqPool()

    async def _fake_get_arq_pool():
        return pool

    monkeypatch.setattr(main_runtime_module, "get_arq_pool", _fake_get_arq_pool)
    return pool


@pytest.fixture
async def internal_client(company_ids):
    """Mirrors test_eval_runner.py's fixture of the same name."""
    company_id = company_ids()
    runtime_app.dependency_overrides[require_internal_auth] = fake_internal_claims(company_id)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            yield client, company_id
    finally:
        runtime_app.dependency_overrides.pop(require_internal_auth, None)


async def test_create_eval_run_endpoint_happy_path_enqueues_job(
    internal_client, fake_arq_pool, make_dataset, make_dataset_item, resolvable_agent_version
):
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    resp = await client.post(
        "/internal/v1/evals",
        json={
            "agent_version_id": agent_version_id,
            "dataset_id": dataset_id,
            "metrics": ["exact_match"],
            "judge_model": None,
            "role": "developer",
            "est_cost_usd": 0.01,
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "queued"
    assert body["agent_version_id"] == agent_version_id
    assert body["dataset_id"] == dataset_id
    assert body["metrics"] == ["exact_match"]
    assert body["est_cost_usd"] == 0.01

    assert fake_arq_pool.enqueued == [("run_eval", (company_id, body["id"]))]


async def test_create_eval_run_endpoint_rejects_unsupported_metric(
    internal_client, fake_arq_pool, make_dataset, make_dataset_item, resolvable_agent_version
):
    """`llm_evaluator`/`python_evaluator` aren't executable yet (eval_runner.py's module
    docstring) - must be rejected with 422 before an eval_runs row is even created or a
    job enqueued, not silently skipped."""
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    resp = await client.post(
        "/internal/v1/evals",
        json={
            "agent_version_id": agent_version_id,
            "dataset_id": dataset_id,
            "metrics": ["exact_match", "python_evaluator"],
            "role": "developer",
        },
    )

    assert resp.status_code == 422
    assert fake_arq_pool.enqueued == []


async def test_create_eval_run_endpoint_rejects_empty_metrics(
    internal_client, fake_arq_pool, make_dataset, make_dataset_item, resolvable_agent_version
):
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    resp = await client.post(
        "/internal/v1/evals",
        json={"agent_version_id": agent_version_id, "dataset_id": dataset_id, "metrics": [], "role": "developer"},
    )

    assert resp.status_code == 422
    assert fake_arq_pool.enqueued == []


async def test_create_eval_run_endpoint_requires_judge_model_for_llm_metric(
    internal_client, fake_arq_pool, make_dataset, make_dataset_item, resolvable_agent_version
):
    """Found via code-review: a requested LLM-based metric with no judge_model would
    otherwise reach dynamiq_adapter.build_llm with `{"model": None}` - present-but-None,
    which defeats its own `config.get("model", "gpt-4o-mini")` fallback (that only
    applies when the key is absent) - must be rejected here, at creation time, instead of
    failing confusingly mid-run."""
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = await resolvable_agent_version(company_id, model="gpt-4o-mini")

    resp = await client.post(
        "/internal/v1/evals",
        json={
            "agent_version_id": agent_version_id,
            "dataset_id": dataset_id,
            "metrics": ["faithfulness"],
            "role": "developer",
        },
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "judge_model_required"
    assert fake_arq_pool.enqueued == []


async def test_create_eval_run_endpoint_malformed_id_is_404(internal_client, fake_arq_pool):
    client, _company_id = internal_client
    resp = await client.post(
        "/internal/v1/evals",
        json={
            "agent_version_id": "not-a-uuid",
            "dataset_id": str(uuid.uuid4()),
            "metrics": ["exact_match"],
            "role": "developer",
        },
    )
    assert resp.status_code == 404
    assert fake_arq_pool.enqueued == []


async def test_create_eval_run_endpoint_missing_dataset_is_404(
    internal_client, fake_arq_pool, resolvable_agent_version
):
    client, company_id = internal_client
    agent_version_id = await resolvable_agent_version(company_id)

    resp = await client.post(
        "/internal/v1/evals",
        json={
            "agent_version_id": agent_version_id,
            "dataset_id": str(uuid.uuid4()),
            "metrics": ["exact_match"],
            "role": "developer",
        },
    )
    assert resp.status_code == 404
    assert fake_arq_pool.enqueued == []


async def test_create_eval_run_endpoint_missing_agent_version_is_404(
    internal_client, fake_arq_pool, make_dataset, make_dataset_item
):
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)

    resp = await client.post(
        "/internal/v1/evals",
        json={
            "agent_version_id": str(uuid.uuid4()),
            "dataset_id": dataset_id,
            "metrics": ["exact_match"],
            "role": "developer",
        },
    )
    assert resp.status_code == 404
    assert fake_arq_pool.enqueued == []


async def test_create_eval_run_endpoint_uncompilable_agent_version_is_422(
    internal_client, fake_arq_pool, make_dataset, make_dataset_item, make_agent_version
):
    client, company_id = internal_client
    dataset_id = make_dataset(company_id)
    make_dataset_item(company_id, dataset_id)
    agent_version_id = make_agent_version(company_id, connection_id=str(uuid.uuid4()))

    resp = await client.post(
        "/internal/v1/evals",
        json={
            "agent_version_id": agent_version_id,
            "dataset_id": dataset_id,
            "metrics": ["exact_match"],
            "role": "developer",
        },
    )
    assert resp.status_code == 422
    assert fake_arq_pool.enqueued == []
