"""Egress-safe HTTP client (PRD §7.4).

All outbound HTTP (HTTP Tools, LLM/connection endpoints, built-in tools) must go through this
client: host checked against the company's `egress_allowlist`
(`app.services.egress_allowlist.list_egress_allowlist`), internal IPs blocked unless the
matching allowlist entry explicitly opts in, no redirects outside the allowlist, 10s timeout,
<=1MB responses.
"""
import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.egress_allowlist import EgressAllowlistEntry

ResolveIpsFn = Callable[[str], Awaitable[list[str]]]

# Internal/private ranges blocked by default per PRD §7.4.
BLOCKED_CIDRS: list[str] = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",  # IPv6 link-local — some cloud providers serve metadata over this too
]
_BLOCKED_NETWORKS = [ipaddress.ip_network(cidr) for cidr in BLOCKED_CIDRS]

DEFAULT_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MB
MAX_REDIRECTS = 5

_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class SafeHttpClientError(Exception):
    """Base for every error this client raises — never a raw network exception."""


class EgressBlockedError(SafeHttpClientError):
    def __init__(self, reason: str, host: str):
        self.reason = reason
        self.host = host
        super().__init__(f"egress_blocked:{reason}:{host}")


class ResponseTooLargeError(SafeHttpClientError):
    def __init__(self, host: str):
        self.host = host
        super().__init__(f"response_too_large:{host}")


class RequestTimeoutError(SafeHttpClientError):
    def __init__(self, host: str):
        self.host = host
        super().__init__(f"request_timeout:{host}")


def parse_host_port(url: str) -> tuple[str, int]:
    """Extracts (host, port) from a URL, defaulting the port from the scheme."""
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ValueError(f"url has no host: {url!r}")
    # `parts.port or default` would be wrong: port 0 is a valid (if unusual) explicit
    # port and is falsy, so it'd be silently replaced by the scheme default instead of
    # being checked/connected to as port 0.
    port = parts.port if parts.port is not None else _DEFAULT_PORTS.get(parts.scheme, 443)
    return host, port


def _host_matches(pattern: str, host: str) -> bool:
    pattern = pattern.lower()
    host = host.lower()
    if pattern == host:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]  # keep the leading dot, e.g. ".example.com"
        return host.endswith(suffix) and host != suffix.lstrip(".")
    return False


def _is_blocked_ip(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    # ::ffff:10.0.0.1 etc. parse as a distinct IPv6Address that's never `in` any of the
    # (IPv4) _BLOCKED_NETWORKS entries by address-family comparison alone — unwrap it to
    # the IPv4 address it actually represents before checking, or it sails straight past
    # every IPv4 block (10.0.0.0/8, 127.0.0.0/8, 169.254.0.0/16, ...).
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in _BLOCKED_NETWORKS)


async def _resolve_ips(host: str) -> list[str]:
    try:
        return [ipaddress.ip_address(host).compressed]
    except ValueError:
        pass  # not a literal IP — resolve via DNS below

    loop = asyncio.get_running_loop()
    addrinfo = await loop.getaddrinfo(host, None)
    return list({info[4][0] for info in addrinfo})


def _resolve_ips_sync(host: str) -> list[str]:
    """Blocking twin of `_resolve_ips`, for transports (e.g. `PinnedTransport`) whose
    `httpx.BaseTransport.handle_request` is called synchronously with no running loop."""
    try:
        return [ipaddress.ip_address(host).compressed]
    except ValueError:
        pass

    addrinfo = socket.getaddrinfo(host, None)
    return list({info[4][0] for info in addrinfo})


def _validated_ip(
    matches: list[EgressAllowlistEntry], resolved_ips: list[str], host: str
) -> str:
    """Picks the IP a now-approved request must connect to. Raises if DNS resolution
    came back empty or (absent an `allow_private_ip` entry) landed on a blocked range."""
    if not resolved_ips:
        raise EgressBlockedError("dns_resolution_failed", host)
    allow_private_ip = any(entry.allow_private_ip for entry in matches)
    if not allow_private_ip:
        for ip in resolved_ips:
            if _is_blocked_ip(ip):
                raise EgressBlockedError("private_ip_blocked", host)
    return resolved_ips[0]


class SafeHttpClient:
    """SSRF-safe HTTP client, gated by a company's `egress_allowlist` rows."""

    def __init__(
        self,
        egress_allowlist: Sequence[EgressAllowlistEntry] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        resolve_ips: ResolveIpsFn | None = None,
    ):
        self.egress_allowlist = list(egress_allowlist or [])
        self._transport = transport
        self._resolve_ips = resolve_ips or _resolve_ips

    def _matching_entries(self, host: str, port: int) -> list[EgressAllowlistEntry]:
        return [
            entry
            for entry in self.egress_allowlist
            if _host_matches(entry.host_pattern, host) and (entry.port is None or entry.port == port)
        ]

    async def check_host(self, host: str, port: int) -> str:
        """Validates `host`/`port` against the allowlist and blocked ranges, returning the
        specific IP the caller must connect to. Callers must connect to that exact IP
        rather than resolving `host` again later — a second, independent resolution would
        let a low-TTL DNS answer flip to a blocked address between this check and the
        real request (DNS-rebinding TOCTOU), silently defeating the check entirely.
        """
        matches = self._matching_entries(host, port)
        if not matches:
            raise EgressBlockedError("not_in_allowlist", host)

        try:
            resolved_ips = await self._resolve_ips(host)
        except OSError:
            # Unresolvable host (NXDOMAIN, resolver timeout, ...) — a plain socket.gaierror
            # here would violate SafeHttpClientError's "never a raw network exception"
            # contract and 500 the endpoint instead of the documented ok:false.
            resolved_ips = []
        return _validated_ip(matches, resolved_ips, host)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        current_url = url
        try:
            # Bounds the *whole* call (every redirect hop plus reading each response
            # body), not just the next `client.send()` — passing `timeout=` to
            # AsyncClient alone resets on every hop, so e.g. 5 redirects at ~9s each
            # could otherwise take ~45s against a module docstring that promises 10s.
            async with asyncio.timeout(DEFAULT_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(
                    timeout=DEFAULT_TIMEOUT_SECONDS, transport=self._transport
                ) as client:
                    for _ in range(MAX_REDIRECTS):
                        host, port = parse_host_port(current_url)
                        pinned_ip = await self.check_host(host, port)

                        request = client.build_request(method, current_url, **kwargs)
                        # Connect to the exact IP just validated (not `host` again) and
                        # keep the original hostname for the Host header (already set
                        # by build_request, above) and TLS SNI/cert verification
                        # (`sni_hostname`) — see check_host's docstring for why
                        # re-resolving here would matter.
                        request.url = request.url.copy_with(host=pinned_ip)
                        request.extensions["sni_hostname"] = host
                        response = await client.send(request, follow_redirects=False, stream=True)
                        try:
                            body = await self._read_capped(response, host)
                        finally:
                            await response.aclose()

                        if (
                            response.status_code in _REDIRECT_STATUS_CODES
                            and "location" in response.headers
                        ):
                            # Simplification: re-issues the same method/body against the
                            # redirect target rather than replicating the browser-style
                            # per-status-code method downgrade (301/302/303 -> GET,
                            # 307/308 preserve) - fine for this client's actual callers
                            # (idempotent GETs), revisit if a caller ever redirects a POST.
                            current_url = urljoin(current_url, response.headers["location"])
                            continue

                        return httpx.Response(
                            response.status_code,
                            headers=response.headers,
                            content=body,
                            request=response.request,
                        )

                    host, _ = parse_host_port(current_url)
                    raise EgressBlockedError("too_many_redirects", host)
        except TimeoutError as exc:
            host, _ = parse_host_port(current_url)
            raise RequestTimeoutError(host) from exc

    @staticmethod
    async def _read_capped(response: httpx.Response, host: str) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError(host)
            chunks.append(chunk)
        return b"".join(chunks)


class PinnedTransport(httpx.HTTPTransport):
    """SSRF-safe `httpx.HTTPTransport` for callers that hand their own `httpx.Client` to a
    third-party SDK (e.g. `openai.OpenAI(http_client=...)`) instead of going through
    `SafeHttpClient.request()` directly — same allowlist + DNS-pinning check as
    `SafeHttpClient`, applied to every request the SDK makes rather than to a one-off
    pre-flight call the SDK's own request could still diverge from.
    """

    def __init__(self, egress_allowlist: Sequence[EgressAllowlistEntry] | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._egress_allowlist = list(egress_allowlist or [])

    def _matching_entries(self, host: str, port: int) -> list[EgressAllowlistEntry]:
        return [
            entry
            for entry in self._egress_allowlist
            if _host_matches(entry.host_pattern, host) and (entry.port is None or entry.port == port)
        ]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        # See parse_host_port's comment — `or default` would wrongly replace an explicit
        # port 0 with the scheme default, since 0 is falsy.
        port = request.url.port if request.url.port is not None else _DEFAULT_PORTS.get(request.url.scheme, 443)

        matches = self._matching_entries(host, port)
        if not matches:
            raise EgressBlockedError("not_in_allowlist", host)

        try:
            resolved_ips = _resolve_ips_sync(host)
        except OSError:
            resolved_ips = []
        pinned_ip = _validated_ip(matches, resolved_ips, host)

        request.url = request.url.copy_with(host=pinned_ip)
        request.extensions["sni_hostname"] = host
        return super().handle_request(request)
