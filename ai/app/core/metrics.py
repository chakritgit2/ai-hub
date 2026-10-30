"""Prometheus metrics (PRD §6.8/§7.1 `/metrics`, cluster-internal scrape only - never
exposed via ingress, per PRD line 496, same as `/healthz`/`/readyz`).

Shared by ai-gateway and ai-runtime: both call `app.services.runs.log_run` on every
completed run, so instrumenting it there covers both services' `/metrics` output with the
same metric names - Prometheus tells gateway and runtime apart via the scrape target's own
`job`/`instance` label, not a label baked into the metric itself.

ai-worker has no HTTP server at all (pure arq queue consumer - see
`deploy/k8s/base/ai-worker-deployment.yaml`'s own comment "No Service/ports/probes"), so
it isn't wired up here; a `queue_depth` gauge for it needs a separate HTTP surface and is
left for a later pass.
"""
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

runs_total = Counter("runs_total", "Completed agent/workflow runs.", ["status", "source"])
run_tokens_total = Counter("run_tokens_total", "Tokens consumed by completed runs.", ["direction"])
run_cost_usd_total = Counter("run_cost_usd_total", "Cost (USD) of completed runs, excluding guardrail cost.")
run_latency_ms = Histogram(
    "run_latency_ms",
    "End-to-end run latency in milliseconds.",
    ["status"],
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000),
)


def render_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
