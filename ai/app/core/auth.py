"""Auth verification stubs.

Real implementations (phase 1 hardening) will verify JWTs against the
relevant JWKS per PRD §7.1/§7.6:

- `verify_runtime_token`: 5-minute JWT issued by console-api
  (`POST /admin/v1/runtime-token`), verified against console-api's JWKS.
- `verify_internal_token`: 60-second JWT signed by console-api
  (`aud=ai-internal`), used for console-api -> ai-runtime `/internal/v1/*`
  calls; also expects `X-Company-Id` and `traceparent` headers (checked by
  callers, not this function).
- `verify_gateway_api_key`: a company-owned gateway API key
  (`ak_{company_code}_...`, stored hashed), resolved via
  `console.resolve_api_key(key_hash, slug)`.

None of these are wired to real signature verification yet - this module
only fixes the function signatures so routers can depend on them.
"""


def verify_runtime_token(token: str) -> dict:
    raise NotImplementedError("verify_runtime_token: JWKS-based verification not yet implemented")


def verify_internal_token(token: str) -> dict:
    raise NotImplementedError("verify_internal_token: JWKS-based verification not yet implemented")


def verify_gateway_api_key(key: str) -> dict:
    raise NotImplementedError("verify_gateway_api_key: resolve_api_key lookup not yet implemented")
