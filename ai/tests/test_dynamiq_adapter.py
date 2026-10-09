"""Unit tests for `app.integrations.dynamiq_adapter._build_openai`'s egress-allowlist
wiring (PRD §7.4) - a custom `api_base` must go through `SafeOpenAIConnection`/
`PinnedTransport`, the provider's own default endpoint must not (see that module's
docstring for why). No network/DB/Postgres required - `PinnedTransport` blocks a
non-allowlisted host before any connection attempt, same as `test_safe_http_client.py`'s
own `PinnedTransport` tests.
"""
import openai
import pytest

from app.integrations.dynamiq_adapter import _build_openai
from app.integrations.safe_http_client import EgressBlockedError
from app.integrations.safe_openai_connection import SafeOpenAIConnection
from app.services.egress_allowlist import EgressAllowlistEntry
from dynamiq.connections.connections import OpenAI as OpenAIConnection


def test_build_openai_without_custom_url_is_unaffected():
    connection, _llm = _build_openai({"api_key": "sk-test"})

    assert type(connection) is OpenAIConnection
    assert not isinstance(connection, SafeOpenAIConnection)


def test_build_openai_with_custom_url_is_egress_checked():
    connection, _llm = _build_openai(
        {"api_key": "sk-test", "url": "https://evil.example.com", "egress_allowlist": []}
    )

    assert isinstance(connection, SafeOpenAIConnection)

    client = connection.connect()
    with pytest.raises(openai.APIError) as exc_info:
        client.models.list()

    # The SDK wraps whatever its transport raises into APIConnectionError - PinnedTransport's
    # EgressBlockedError ends up here as __cause__ (same pattern asserted in
    # main_runtime.py's testConnection).
    assert isinstance(exc_info.value.__cause__, EgressBlockedError)
    assert exc_info.value.__cause__.reason == "not_in_allowlist"


def test_build_openai_blocked_host_disables_retries():
    """A host with no matching allowlist entry will be rejected identically on every
    attempt (PinnedTransport's check is a pure pattern match, no DNS/network) - the
    openai SDK retries any non-OpenAIError/timeout exception by default, so without this
    a blocked connection would burn ~2s of pointless exponential-backoff retries on every
    call (measured before this fix), including real agent runs, not just a one-off probe."""
    connection, _llm = _build_openai(
        {"api_key": "sk-test", "url": "https://evil.example.com", "egress_allowlist": []}
    )

    assert connection.connect().max_retries == 0


def test_build_openai_allowlisted_host_keeps_default_retries():
    """A host that *is* allowlisted might still fail for a genuine transient
    provider/network reason - those should still get the SDK's normal retry behavior,
    not be silently starved of resilience just because this connection has a custom
    api_base."""
    allowlist = [EgressAllowlistEntry(host_pattern="api.openrouter.ai", port=None, allow_private_ip=False)]
    connection, _llm = _build_openai(
        {"api_key": "sk-test", "url": "https://api.openrouter.ai/v1", "egress_allowlist": allowlist}
    )

    assert connection.connect().max_retries == openai.DEFAULT_MAX_RETRIES
