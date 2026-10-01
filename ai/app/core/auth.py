"""Auth verification against console-api's JWKS (PRD §7.1/§7.6).

- `verify_runtime_token`: 5-minute JWT issued by console-api
  (`POST /admin/v1/runtime-token`), carrying `company_id`, `user_id`, `role`,
  `agent_version_id` — required by the Playground endpoints.
- `verify_internal_token`: 60-second JWT signed by console-api (`aud=ai-internal`),
  used for console-api -> ai-runtime `/internal/v1/*` calls; also expects
  `X-Company-Id` and `traceparent` headers (checked by callers, not this function).
- `verify_gateway_api_key`: a company-owned gateway API key
  (`ak_{company_code}_...`, stored hashed), resolved via
  `console.resolve_api_key(key_hash, slug)` (`app.services.api_keys`) — unlike the two
  JWT verifiers above, this isn't JWKS-based at all: the key is a bearer secret hashed
  and looked up directly in Postgres, not a signed token.

Both JWT verifiers share one JWKS (console-api signs both token types with the
same key, distinguished only by `aud`) and cache it in-process for
`_JWKS_CACHE_TTL_SECONDS` so a verification doesn't do an HTTP round trip per
request.
"""
import time

import httpx
import jwt

from app.core.config import get_settings

_JWKS_CACHE_TTL_SECONDS = 300
_jwks_cache: dict[str, tuple[float, list[dict]]] = {}


class InvalidTokenError(Exception):
    """A token failed signature/claim verification — callers map this to HTTP 401."""


def _fetch_jwks_keys(url: str) -> list[dict]:
    cached = _jwks_cache.get(url)
    if cached is not None and (time.monotonic() - cached[0]) < _JWKS_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        response = httpx.get(url, timeout=5.0)
        response.raise_for_status()
        keys = response.json().get("keys", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"auth: failed to fetch JWKS from {url}: {exc}") from exc

    _jwks_cache[url] = (time.monotonic(), keys)
    return keys


def _verify(token: str, *, audience: str, issuer: str) -> dict:
    settings = get_settings()

    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError(f"invalid_token: {exc}") from exc

    kid = unverified_header.get("kid")
    keys = _fetch_jwks_keys(settings.CONSOLE_JWKS_URL)
    key_data = next((k for k in keys if k.get("kid") == kid), None)
    if key_data is None:
        raise InvalidTokenError("invalid_token: kid not found in JWKS")

    try:
        signing_key = jwt.PyJWK(key_data).key
        return jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError(f"invalid_token: {exc}") from exc


def verify_runtime_token(token: str) -> dict:
    settings = get_settings()
    payload = _verify(token, audience=settings.RUNTIME_TOKEN_AUD, issuer=settings.CONSOLE_INTERNAL_JWT_ISSUER)

    missing = [c for c in ("company_id", "user_id", "role", "agent_version_id") if c not in payload]
    if missing:
        raise InvalidTokenError(f"invalid_token: missing claims {missing}")

    return payload


def verify_internal_token(token: str) -> dict:
    settings = get_settings()
    return _verify(token, audience=settings.CONSOLE_INTERNAL_JWT_AUD, issuer=settings.CONSOLE_INTERNAL_JWT_ISSUER)


async def verify_gateway_api_key(key: str, slug: str) -> dict | None:
    """Returns `{"company_id": ..., "deployment_id": ..., "allowed_ips": [...]}`, or
    `None` when the key doesn't exist, belongs to another company, or isn't scoped to
    this deployment - all indistinguishable. Deliberately does NOT raise
    `InvalidTokenError` like the two JWT verifiers above (which callers map to 401): PRD
    §7.7/§12 require this specific case to come back as 404 "as if the deployment did
    not exist", not 401, so callers must map `None` to 404 themselves, not reuse the 401
    convention. `allowed_ips` (PRD §7.7) is returned, not enforced here, since this
    function has no access to the caller's request - callers check it against
    `request.client.host` themselves."""
    from app.services.api_keys import resolve_gateway_api_key

    resolved = await resolve_gateway_api_key(key, slug)
    if resolved is None:
        return None

    return {
        "company_id": resolved.company_id,
        "deployment_id": resolved.deployment_id,
        "allowed_ips": resolved.allowed_ips,
    }
