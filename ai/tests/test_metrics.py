import uuid

from app.core.metrics import runs_total
from app.services.runs import RunUsage, log_run

from .markers import requires_postgres


def test_gateway_exposes_metrics_in_prometheus_text_format(gateway_client) -> None:
    resp = gateway_client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


def test_runtime_exposes_metrics_in_prometheus_text_format(runtime_client) -> None:
    resp = runtime_client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


@requires_postgres
async def test_log_run_increments_the_shared_counters_and_histogram(gateway_client) -> None:
    company_id = str(uuid.uuid4())
    await log_run(
        run_id=str(uuid.uuid4()),
        company_id=company_id,
        trace_id=str(uuid.uuid4()),
        status="success",
        source="gateway",
        usage=RunUsage(tokens_in=10, tokens_out=5, cost_usd=0.002),
        latency_ms=120,
    )

    body = gateway_client.get("/metrics").text

    assert 'runs_total{source="gateway",status="success"}' in body
    assert 'run_tokens_total{direction="in"}' in body
    assert 'run_tokens_total{direction="out"}' in body
    assert "run_cost_usd_total" in body
    assert 'run_latency_ms_bucket{le="250.0",status="success"}' in body


async def test_log_run_counts_the_metric_even_with_no_company_id_to_scope_the_db_write_to() -> None:
    """The `logs.runs` write is a documented no-op for a blank `company_id` (runs.py's own
    docstring), but that's a DB-scoping concern, not a reason to undercount a real
    completed run in metrics that carry no company label - no Postgres needed here since
    this path never touches the DB."""
    counter = runs_total.labels(status="success", source="no-company-test")
    before_value = counter._value.get()

    await log_run(run_id=str(uuid.uuid4()), company_id="", trace_id=str(uuid.uuid4()), status="success",
                   source="no-company-test")

    assert counter._value.get() == before_value + 1
