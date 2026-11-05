# Grafana dashboards

This repo does **not** run its own Prometheus/Grafana — the cluster has a single shared
stack in namespace `monitoring` that every app registers into (infra's 2026-11 decision,
to avoid duplicate Prometheus/Grafana instances per namespace).

How ai-gateway/ai-runtime get scraped: their Services in
[deploy/k8s/base/ai-gateway-deployment.yaml](../k8s/base/ai-gateway-deployment.yaml) and
[ai-runtime-deployment.yaml](../k8s/base/ai-runtime-deployment.yaml) carry
`prometheus.io/scrape`/`port`/`path` annotations that the shared Prometheus's
`kubernetes-service-endpoints` job auto-discovers. There is no Prometheus Operator in this
cluster — don't add a ServiceMonitor/PodMonitor, and don't annotate the pod template as
well as the Service (the shared Prometheus also runs a `kubernetes-pods` job, so that would
double-scrape and double every `sum()`).

`dynamiq-runs-overview.json` is this file tree's copy of the dashboard imported into the
shared Grafana — source of truth for re-importing after an edit, not something this repo
provisions automatically. Panels query `app/core/metrics.py`'s `runs_total`,
`run_latency_ms`, `run_tokens_total`, `run_cost_usd_total`, each filtered with
`namespace="ai-hub-advws"` (the Prometheus is shared with other systems, so an unfiltered
query would mix in other namespaces' same-named metrics if any exist) and using a
`${datasource}` template variable rather than a hardcoded datasource uid (the shared
Grafana's Prometheus uid isn't guaranteed to be `"prometheus"`).

Scrape interval on the shared Prometheus is 1m (not 15s) with 90d retention — `rate(...[5m])`
queries are fine at that interval; if a future metric genuinely needs 15s resolution, that
needs a dedicated scrape job from infra, not a per-namespace Prometheus.

Tempo (traces, [deploy/k8s/base/tempo-deployment.yaml](../k8s/base/tempo-deployment.yaml))
is unaffected by this — it's this namespace's own instance, added as a second datasource,
named `Tempo (ai-hub)`, in the same shared Grafana. Like the Prometheus/Loki datasources
already there, it was added through the Grafana UI (`http://tempo.ai-hub-advws.svc.cluster
.local:3200`) rather than provisioning — that Grafana deployment has no provisioning
sidecar, so there's no ConfigMap-based datasource config to keep in this repo for it.
