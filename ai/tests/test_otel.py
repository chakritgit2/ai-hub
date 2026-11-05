from fastapi import FastAPI

from app.core.config import get_settings
from app.core.otel import setup_tracing


def test_noop_when_otlp_endpoint_unset(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OTEL_EXPORTER_OTLP_ENDPOINT", None)
    calls = []
    monkeypatch.setattr("app.core.otel.openlit.init", lambda **kwargs: calls.append(kwargs))

    setup_tracing(FastAPI(title="ai-runtime"))

    assert calls == []


def test_initializes_openlit_when_otlp_endpoint_set(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo.internal:4318")
    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "production")
    calls = []
    monkeypatch.setattr("app.core.otel.openlit.init", lambda **kwargs: calls.append(kwargs))

    setup_tracing(FastAPI(title="ai-runtime"))

    assert calls == [
        {
            "application_name": "ai-runtime",
            "environment": "production",
            "otlp_endpoint": "http://tempo.internal:4318",
            "capture_message_content": False,
            "disable_metrics": True,
            "disabled_instrumentors": [
                "litellm",
                "openai",
                "anthropic",
                "bedrock",
                "vertexai",
                "google-ai-studio",
            ],
        }
    ]
