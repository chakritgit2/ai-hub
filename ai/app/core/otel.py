"""LLM-call observability via OpenLIT (PRD §6.8/§7.1).

OpenLIT auto-instruments every GenAI/agent-framework library it finds
installed in this process - here that's `dynamiq` itself (its own
`Agent.run`/`Workflow.run`/`Memory.*` spans) and, since dynamiq's LLM nodes
call `litellm.completion()`/`acompletion()` internally rather than the
`openai` SDK directly (see `app/integrations/dynamiq_adapter.py`), `litellm`
too - verified with a live spike (2026-11-02): patching `litellm.completion`
still fires correctly even though nothing in this codebase imports or calls
litellm directly, because dynamiq's own `from litellm import completion` is a
fresh, lazy, per-call import that always resolves to whatever is currently
bound in the `litellm` module namespace at call time.

A no-op (same as before) when OTEL_EXPORTER_OTLP_ENDPOINT isn't set, so local
dev needs no Tempo collector running.

`capture_message_content=False`: `logs.runs.input`/`output` already hold this
same prompt/completion text (PRD §6.8/§6.9), gated behind RunsController's
write-role check (see `replayRun()`) - Tempo/Grafana has no equivalent
per-company access control, so duplicating raw message content into spans
would be a second, wider-open copy of the same customer data.

`disable_metrics=True`: `app/core/metrics.py`'s own Prometheus counters
(`runs_total`, `run_tokens_total`, `run_cost_usd_total`, `run_latency_ms`) are
already the system of record for token/cost metrics - this is for distributed
tracing only, not a second metrics pipeline.

`disabled_instrumentors`: found 2026-11-05 that OpenLIT 1.45.0 (already the
latest PyPI release - no upgrade fixes this) silently swallows the exception
when a GenAI-provider instrumentor fails to instantiate/attach
(`openlit.get_all_instrumentors`'s `except Exception: pass`,
`openlit._instrumentors.get_instrumentor_class`'s `except Exception: return
None` - neither logs anything). litellm/openai/anthropic/bedrock/vertexai/
google-ai-studio were all silently never instrumented this way (confirmed:
zero "enabled"/"not found"/"Failed to instrument" log lines for any of them,
while every other instrumentor - fastapi, httpx, requests, starlette, the
not-installed ones - logged correctly), most likely litellm's internal
module layout having drifted from what this openlit version expects.
`app/services/runtime.py`'s `_StepCollector` now creates these LLM/tool-call
spans itself via `opentelemetry-api` directly instead - disabling OpenLIT's
own (currently broken, and now redundant) attempt at the same libraries
avoids a silently-wasted init-time attempt today and, if OpenLIT fixes this
upstream later, avoids doubled-up/nested spans for the same call.
"""
import openlit
from fastapi import FastAPI

from app.core.config import get_settings


def setup_tracing(app: FastAPI) -> None:
    settings = get_settings()
    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        return

    openlit.init(
        application_name=app.title,
        environment=settings.ENVIRONMENT,
        otlp_endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        capture_message_content=False,
        disable_metrics=True,
        disabled_instrumentors=[
            "litellm",
            "openai",
            "anthropic",
            "bedrock",
            "vertexai",
            "google-ai-studio",
        ],
    )
