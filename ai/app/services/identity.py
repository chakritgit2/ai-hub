"""Delegated identity token minting (PRD §7.5).

Mints a 60-second RS256 JWT (aud = tool host) with claims `company_id`,
`sub` (external_user_id), `claims`, `act` (the agent/deployment acting),
`run_id`, attached to every `auth_mode: delegated` tool call
(`app.integrations.safe_http_tool.SafeHttpApiCall`). Target systems verify it
against the JWKS ai-gateway publishes at `GET /.well-known/jwks.json` and
authorize by `sub`.

Signs with ai-gateway's own keypair (`app.services.gateway_auth`), the same
one that already publishes that JWKS - not a separate key, since the whole
point of PRD §7.6's "one JWKS" design is a tool host verifies both session
tokens and delegated tool-call tokens the same way.
"""
from dataclasses import dataclass, field
from time import time
from uuid import uuid4

import jwt

from app.services.gateway_auth import _kid, _load_or_create_key

_TOKEN_TTL_SECONDS = 60


@dataclass(frozen=True)
class DelegatedIdentityContext:
    """Per-run identity to mint delegated tokens from - built once per agent
    run in `app.services.runtime.execute_agent_run` and threaded into every
    `SafeHttpApiCall` the run's tools are built with."""

    sub: str = ""
    claims: dict = field(default_factory=dict)
    act: str = ""
    run_id: str = ""


def mint_delegated_token(company_id: str, sub: str, claims: dict, act: str, run_id: str, audience: str) -> str:
    key = _load_or_create_key()
    now = int(time())
    payload = {
        "aud": audience,
        "company_id": company_id,
        "sub": sub,
        "claims": claims,
        "act": act,
        "run_id": run_id,
        "iat": now,
        "exp": now + _TOKEN_TTL_SECONDS,
        "jti": str(uuid4()),
    }
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": _kid()})
