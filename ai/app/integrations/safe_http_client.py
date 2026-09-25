"""Egress-safe HTTP client stub (PRD §7.4).

All outbound HTTP (HTTP Tools, LLM/connection endpoints, built-in tools) must
go through this client: host checked against `egress_allowlist`, internal
IPs blocked unless explicitly allowed, no redirects outside the allowlist,
10s timeout, <=1MB responses.
"""
from typing import Any

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

DEFAULT_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MB


class SafeHttpClient:
    """SSRF-safe HTTP client.

    Real implementation should:
    - Resolve the target host and reject any address falling in
      `BLOCKED_CIDRS` unless the host is present in the caller's
      `egress_allowlist` with `allow_private_ip=True`.
    - Refuse to follow redirects to hosts outside the allowlist.
    - Enforce `DEFAULT_TIMEOUT_SECONDS` and truncate/reject responses over
      `MAX_RESPONSE_BYTES`.
    """

    def __init__(self, egress_allowlist: list[dict[str, Any]] | None = None):
        self.egress_allowlist = egress_allowlist or []

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        raise NotImplementedError("SafeHttpClient.request: SSRF-safe HTTP client not yet implemented")
