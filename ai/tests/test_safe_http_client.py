import asyncio

import httpx
import pytest

import app.integrations.safe_http_client as safe_http_client_module
from app.integrations.safe_http_client import (
    MAX_RESPONSE_BYTES,
    EgressBlockedError,
    PinnedTransport,
    RequestTimeoutError,
    ResponseTooLargeError,
    SafeHttpClient,
    parse_host_port,
)
from app.services.egress_allowlist import EgressAllowlistEntry


def _entry(host_pattern: str, port: int | None = None, allow_private_ip: bool = False) -> EgressAllowlistEntry:
    return EgressAllowlistEntry(host_pattern=host_pattern, port=port, allow_private_ip=allow_private_ip)


async def _fake_public_resolver(host: str) -> list[str]:
    """Stands in for real DNS resolution in tests — hostname-matching/redirect/response-cap
    behavior shouldn't depend on real network access or real DNS records existing for
    made-up test domains. Always resolves to a real, non-blocked public IP."""
    return ["93.184.216.34"]


def test_parse_host_port_defaults_https_port():
    assert parse_host_port("https://api.openai.com/v1") == ("api.openai.com", 443)


def test_parse_host_port_defaults_http_port():
    assert parse_host_port("http://internal.example.com/x") == ("internal.example.com", 80)


def test_parse_host_port_uses_explicit_port():
    assert parse_host_port("https://api.openai.com:8443/v1") == ("api.openai.com", 8443)


def test_parse_host_port_preserves_explicit_port_zero():
    """`parts.port or default` would treat port 0 as falsy and silently substitute the
    scheme default — 0 is unusual but a syntactically valid explicit port."""
    assert parse_host_port("http://internal.example.com:0/") == ("internal.example.com", 0)


async def test_check_host_blocks_ipv4_mapped_ipv6_loopback():
    """::ffff:127.0.0.1 is `127.0.0.1` wearing an IPv6 wrapper — _is_blocked_ip must unwrap
    it before comparing against the (IPv4) BLOCKED_CIDRS or it sails straight through."""

    async def resolver(host: str) -> list[str]:
        return ["::ffff:127.0.0.1"]

    with pytest.raises(EgressBlockedError) as exc_info:
        await SafeHttpClient([_entry("internal.example.com")], resolve_ips=resolver).check_host(
            "internal.example.com", 443
        )
    assert exc_info.value.reason == "private_ip_blocked"


async def test_check_host_blocks_link_local_ipv6():
    with pytest.raises(EgressBlockedError) as exc_info:
        await SafeHttpClient([_entry("fe80::1")]).check_host("fe80::1", 443)
    assert exc_info.value.reason == "private_ip_blocked"


async def test_check_host_blocks_when_dns_resolution_fails():
    """An unresolvable host must become EgressBlockedError, not a raw socket.gaierror —
    SafeHttpClientError is the only kind of exception this client is allowed to raise."""

    async def failing_resolver(host: str) -> list[str]:
        raise OSError("nodename nor servname provided, or not known")

    with pytest.raises(EgressBlockedError) as exc_info:
        await SafeHttpClient([_entry("nonexistent.invalid")], resolve_ips=failing_resolver).check_host(
            "nonexistent.invalid", 443
        )
    assert exc_info.value.reason == "dns_resolution_failed"


async def test_check_host_allows_exact_match():
    client = SafeHttpClient([_entry("api.openai.com")], resolve_ips=_fake_public_resolver)
    await client.check_host("api.openai.com", 443)


async def test_check_host_allows_wildcard_match():
    client = SafeHttpClient([_entry("*.example.com")], resolve_ips=_fake_public_resolver)
    await client.check_host("api.example.com", 443)


async def test_check_host_blocks_wildcard_bare_domain():
    client = SafeHttpClient([_entry("*.example.com")], resolve_ips=_fake_public_resolver)
    with pytest.raises(EgressBlockedError, match="not_in_allowlist"):
        await client.check_host("example.com", 443)


async def test_check_host_blocks_host_not_in_allowlist():
    with pytest.raises(EgressBlockedError) as exc_info:
        await SafeHttpClient([_entry("api.openai.com")]).check_host("evil.example.com", 443)
    assert exc_info.value.reason == "not_in_allowlist"


async def test_check_host_blocks_when_port_does_not_match():
    with pytest.raises(EgressBlockedError) as exc_info:
        await SafeHttpClient([_entry("api.openai.com", port=8443)]).check_host("api.openai.com", 443)
    assert exc_info.value.reason == "not_in_allowlist"


async def test_check_host_allows_any_port_when_entry_port_is_none():
    client = SafeHttpClient([_entry("api.openai.com")], resolve_ips=_fake_public_resolver)
    await client.check_host("api.openai.com", 8443)


async def test_check_host_blocks_private_ip_by_default():
    with pytest.raises(EgressBlockedError) as exc_info:
        await SafeHttpClient([_entry("10.0.0.5")]).check_host("10.0.0.5", 443)
    assert exc_info.value.reason == "private_ip_blocked"


async def test_check_host_allows_private_ip_when_entry_opts_in():
    await SafeHttpClient([_entry("10.0.0.5", allow_private_ip=True)]).check_host("10.0.0.5", 443)


async def test_check_host_blocks_loopback():
    with pytest.raises(EgressBlockedError) as exc_info:
        await SafeHttpClient([_entry("127.0.0.1")]).check_host("127.0.0.1", 443)
    assert exc_info.value.reason == "private_ip_blocked"


async def test_check_host_blocks_link_local_metadata_address():
    """169.254.169.254 — the classic cloud-metadata SSRF target (PRD §13)."""
    with pytest.raises(EgressBlockedError) as exc_info:
        await SafeHttpClient([_entry("169.254.169.254")]).check_host("169.254.169.254", 80)
    assert exc_info.value.reason == "private_ip_blocked"


async def test_request_returns_response_from_allowed_host():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = SafeHttpClient(
        [_entry("api.example.com")], transport=httpx.MockTransport(handler), resolve_ips=_fake_public_resolver
    )
    response = await client.request("GET", "https://api.example.com/v1/models")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_request_blocks_host_not_in_allowlist_without_calling_transport():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    client = SafeHttpClient([], transport=httpx.MockTransport(handler))

    with pytest.raises(EgressBlockedError):
        await client.request("GET", "https://evil.example.com/")

    assert called is False


async def test_request_follows_redirect_to_allowed_host():
    def handler(request: httpx.Request) -> httpx.Response:
        # SafeHttpClient pins request.url to the resolved IP (DNS-rebinding guard) and
        # keeps the real hostname only in the Host header/SNI extension — so the mock
        # transport must key off that, not request.url.host, to tell the two hops apart.
        if request.headers["host"] == "old.example.com":
            return httpx.Response(302, headers={"location": "https://new.example.com/"})
        return httpx.Response(200, json={"from": "new"})

    client = SafeHttpClient(
        [_entry("old.example.com"), _entry("new.example.com")],
        transport=httpx.MockTransport(handler),
        resolve_ips=_fake_public_resolver,
    )
    response = await client.request("GET", "https://old.example.com/")

    assert response.status_code == 200
    assert response.json() == {"from": "new"}


async def test_request_blocks_redirect_to_disallowed_host():
    def handler(request: httpx.Request) -> httpx.Response:
        # See test_request_follows_redirect_to_allowed_host — match on the Host header,
        # since request.url.host is the pinned IP by the time the transport sees it.
        if request.headers["host"] == "old.example.com":
            return httpx.Response(302, headers={"location": "https://evil.example.com/"})
        return httpx.Response(200)  # pragma: no cover - must never be reached

    client = SafeHttpClient(
        [_entry("old.example.com")], transport=httpx.MockTransport(handler), resolve_ips=_fake_public_resolver
    )

    with pytest.raises(EgressBlockedError) as exc_info:
        await client.request("GET", "https://old.example.com/")

    assert exc_info.value.reason == "not_in_allowlist"


async def test_request_connects_to_the_resolved_ip_not_the_hostname():
    """Regression guard for the DNS-rebinding TOCTOU: check_host's resolved IP must be
    what the transport actually connects to (request.url.host), with the real hostname
    surviving only in the Host header/SNI — re-resolving `host` by name at connect time
    (instead of reusing the already-validated IP) is exactly what would let a low-TTL DNS
    answer swap to a blocked address between the check and the real request."""
    seen_url_host = None
    seen_request_host_header = None
    seen_sni_hostname = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url_host, seen_request_host_header, seen_sni_hostname
        seen_url_host = request.url.host
        seen_request_host_header = request.headers["host"]
        seen_sni_hostname = request.extensions.get("sni_hostname")
        return httpx.Response(200)

    client = SafeHttpClient(
        [_entry("api.example.com")], transport=httpx.MockTransport(handler), resolve_ips=_fake_public_resolver
    )
    await client.request("GET", "https://api.example.com/v1/models")

    assert seen_url_host == "93.184.216.34"
    assert seen_request_host_header == "api.example.com"
    assert seen_sni_hostname == "api.example.com"


def test_pinned_transport_blocks_host_not_in_allowlist_without_connecting():
    transport = PinnedTransport([])
    request = httpx.Request("GET", "https://evil.example.com/")

    with pytest.raises(EgressBlockedError) as exc_info:
        transport.handle_request(request)

    assert exc_info.value.reason == "not_in_allowlist"


async def test_request_total_timeout_bounds_the_whole_redirect_chain(monkeypatch):
    """A timeout passed to httpx.AsyncClient resets on every hop — this client's own
    docstring promises a single 10s budget for the whole call, redirects included."""
    monkeypatch.setattr(safe_http_client_module, "DEFAULT_TIMEOUT_SECONDS", 0.1)

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.08)
        # Keeps redirecting to itself — each hop alone fits under the 0.1s budget, but
        # the second hop pushes the cumulative time past it.
        return httpx.Response(302, headers={"location": "https://old.example.com/"})

    client = SafeHttpClient(
        [_entry("old.example.com")], transport=httpx.MockTransport(handler), resolve_ips=_fake_public_resolver
    )

    with pytest.raises(RequestTimeoutError):
        await client.request("GET", "https://old.example.com/")


async def test_request_rejects_oversized_response():
    oversized_body = b"x" * (MAX_RESPONSE_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized_body)

    client = SafeHttpClient(
        [_entry("api.example.com")], transport=httpx.MockTransport(handler), resolve_ips=_fake_public_resolver
    )

    with pytest.raises(ResponseTooLargeError):
        await client.request("GET", "https://api.example.com/")
