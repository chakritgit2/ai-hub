"""OpenTelemetry wiring stub.

Real implementation (PRD §6.8/§7.1): console-api propagates `traceparent` to
ai-runtime and on to HTTP Tools so traces span systems; Grafana Tempo lands
in phase 3. For now this is a documented no-op so `main_runtime.py` /
`main_gateway.py` have a stable call site to wire real tracing into later.
"""
from fastapi import FastAPI


def setup_tracing(app: FastAPI) -> None:
    pass
