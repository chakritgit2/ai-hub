"""Delegated identity token minting stub (PRD §7.5).

Real implementation mints a 60-second RS256 JWT (aud = tool host) with
claims `company_id`, `sub` (external_user_id), `claims`, `act` (the
agent/deployment acting), `run_id`, before each `auth_mode: delegated` tool
call. Target systems verify it against the JWKS ai-gateway publishes at
`GET /.well-known/jwks.json` and authorize by `sub`.
"""


def mint_delegated_token(company_id: str, sub: str, claims: dict, act: str, run_id: str) -> str:
    raise NotImplementedError("mint_delegated_token: RS256 delegated identity tokens not yet implemented")
