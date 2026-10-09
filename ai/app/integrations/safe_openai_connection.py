"""Egress-safe `dynamiq.connections.OpenAI` (PRD §7.4).

`OpenAIConnection.connect()` builds a plain `openai.OpenAI(api_key=..., base_url=...)`
client with no way to inject a transport. A custom (company-supplied) `api_base` is a
potentially-attacker-influenced URL exactly like an HTTP Tool's, so it must go through
the same SSRF/egress-allowlist protection - `app.integrations.safe_http_client`'s own
module docstring says "All outbound HTTP ... LLM/connection endpoints ... must go
through this client". This subclass overrides `connect()` to hand `openai.OpenAI` a
`PinnedTransport`-backed `httpx.Client` instead - the same approach `main_runtime.py`'s
`testConnection` probe already used for its own disposable client, now applied to the
connection object real agent runs actually use.

Only meant for a *custom* api_base (see `app.integrations.dynamiq_adapter._build_openai`)
- the provider's own default endpoint isn't a company-controlled SSRF vector and most
companies won't have an egress_allowlist entry for it at all, so that case still uses a
plain `OpenAIConnection` unchanged.

`connect()` pre-checks the allowlist itself (via `host_in_allowlist`, a pure pattern
match - no DNS/network) rather than relying on `PinnedTransport` to raise on the first
real request: the `openai` SDK retries *any* non-`OpenAIError`/timeout exception from its
transport, `EgressBlockedError` included, so without this a blocked host would silently
eat ~2 real seconds of pointless exponential-backoff retries (measured) before finally
surfacing - on every call, including real agent runs, not just the one-off "Test
Connection" probe this logic was lifted from. Pre-checking lets a definitely-blocked host
fail in milliseconds via `max_retries=0`, while a host that *is* allowlisted keeps the
SDK's normal retry behavior for genuine transient provider/network errors.
"""
from typing import Any

import httpx
import openai
from pydantic import ConfigDict, Field

from dynamiq.connections.connections import OpenAI as OpenAIConnection

from app.integrations.safe_http_client import PinnedTransport, host_in_allowlist, parse_host_port
from app.services.egress_allowlist import EgressAllowlistEntry


class SafeOpenAIConnection(OpenAIConnection):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    egress_allowlist: list[EgressAllowlistEntry] = Field(default_factory=list)

    def connect(self) -> Any:
        from openai import OpenAI as OpenAIClient

        host, port = parse_host_port(self.url)
        max_retries = openai.DEFAULT_MAX_RETRIES if host_in_allowlist(self.egress_allowlist, host, port) else 0

        return OpenAIClient(
            api_key=self.api_key,
            base_url=self.url,
            http_client=httpx.Client(transport=PinnedTransport(self.egress_allowlist)),
            max_retries=max_retries,
        )
