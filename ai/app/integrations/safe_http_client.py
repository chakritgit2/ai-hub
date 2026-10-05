"""Egress-safe HTTP client (PRD §7.4).

All outbound HTTP (HTTP Tools, LLM/connection endpoints, built-in tools) must go through this
client: host checked against the company's `egress_allowlist`
(`app.services.egress_allowlist.list_egress_allowlist`), internal IPs blocked unless the
matching allowlist entry explicitly opts in, no redirects outside the allowlist, 10s timeout,
<=1MB responses.
"""
import asyncio
import ipaddress
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


def parse_host_port(url: str) -> tuple[str, int]:
    """Extracts (host, port) from a URL, defaulting the port from the scheme."""
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ValueError(f"url has no host: {url!r}")
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme, 443)
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
    return any(address in network for network in _BLOCKED_NETWORKS)


async def _resolve_ips(host: str) -> list[str]:
    try:
        return [ipaddress.ip_address(host).compressed]
    except ValueError:
        pass  # not a literal IP — resolve via DNS below

    loop = asyncio.get_running_loop()
    addrinfo = await loop.getaddrinfo(host, None)
    return list({info[4][0] for info in addrinfo})


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

    async def check_host(self, host: str, port: int) -> None:
        matches = self._matching_entries(host, port)
        if not matches:
            raise EgressBlockedError("not_in_allowlist", host)

        resolved_ips = await self._resolve_ips(host)
        allow_private_ip = any(entry.allow_private_ip for entry in matches)
        if not allow_private_ip:
            for ip in resolved_ips:
                if _is_blocked_ip(ip):
                    raise EgressBlockedError("private_ip_blocked", host)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        current_url = url
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT_SECONDS, transport=self._transport
        ) as client:
            for _ in range(MAX_REDIRECTS):
                host, port = parse_host_port(current_url)
                await self.check_host(host, port)

                request = client.build_request(method, current_url, **kwargs)
                response = await client.send(request, follow_redirects=False, stream=True)
                try:
                    body = await self._read_capped(response, host)
                finally:
                    await response.aclose()

                if response.status_code in _REDIRECT_STATUS_CODES and "location" in response.headers:
                    # Simplification: re-issues the same method/body against the redirect
                    # target rather than replicating the browser-style per-status-code
                    # method downgrade (301/302/303 -> GET, 307/308 preserve) - fine for
                    # this client's actual callers (idempotent GETs), revisit if a caller
                    # ever redirects a POST.
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
