"""ai-gateway FastAPI app.

Serves the public gateway API (PRD §9.4): `/v1/deployments/{slug}/*` (API
key / session token auth) and the unauthenticated JWKS endpoint used by tool
targets to verify delegated identity tokens (PRD §7.5/7.6).

Every route's function name matches the `operationId` in
`dynamiq-console/contracts/openapi/ai-public.yaml` exactly. REAL: `runDeployment`,
`createDeploymentSession`, `getDeploymentInfo`, `getJwks` - API key / session token auth
(`require_gateway_auth`/`require_gateway_api_key`), real quota reservation/reconciliation
and rate limiting (`app.services.runtime.run_deployment_agent`), CORS validation against
a deployment's own `allowed_origins` for session-token/browser callers. `streamDeployment`
(SSE) and `decideApproval` (Phase 2 per the contract's own `x-phase: 2`) remain 501 stubs -
explicit scope boundaries, not gaps.
"""
import logging
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from app.core.auth import verify_gateway_api_key
from app.core.config import get_settings
from app.core.otel import setup_tracing
from app.services.agent_spec import AgentSpecDoc
from app.services.agent_versions import resolve_agent_version
from app.services.deployments import DeploymentRow, resolve_deployment, resolve_deployment_id_by_slug
from app.services.gateway_auth import get_jwks, mint_session_token, verify_session_token
from app.services.quotas import QuotaExceededError
from app.services.runtime import run_deployment_agent

logger = logging.getLogger(__name__)

app = FastAPI(title="ai-gateway")
setup_tracing(app)

# External callers have no admin/developer/viewer identity of their own (unlike
# Playground, which gets `role` from a verified runtime token minted for a logged-in
# console user) - every deployment run compiles with the compiler's least-privileged role,
# so an admin-only node type (e.g. code execution, PRD §6.1 Non-goal #3) can never run
# through a published deployment regardless of who published it.
_GATEWAY_RUN_ROLE = "viewer"


def _not_implemented(operation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"error": "not_implemented", "operation_id": operation_id},
    )


def _extract_bearer(authorization: str | None) -> str:
    """Accepts either `Bearer <value>` (the contract's `sessionToken` scheme) or the raw
    value alone (the contract's `gatewayApiKey` scheme is `type: apiKey`, not `http
    bearer` - no prefix implied), so a caller using either convention for either
    credential kind still works."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing_authorization_header")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1]
    return authorization


@dataclass(frozen=True)
class GatewayIdentity:
    company_id: str
    deployment_id: str
    auth_kind: Literal["api_key", "session_token"]
    external_user_id: str | None = None
    claims: dict = field(default_factory=dict)


def _client_ip(request: Request) -> str | None:
    """The real caller's IP for `allowed_ips` matching (PRD §7.7). `request.client.host` is
    only the gateway's direct TCP peer - behind a reverse proxy/load balancer that's the
    proxy itself, not the caller, so matching it directly against a configured allowlist
    would reject every real client (or admit everyone, if the proxy's own IP happened to be
    listed). Only trust `X-Forwarded-For`'s left-most (original client) entry when the
    direct peer is itself one of the operator's own `TRUSTED_PROXY_IPS` - otherwise any
    caller could set that header itself to spoof its way past the allowlist."""
    if request.client is None:
        return None

    direct_peer = request.client.host
    trusted_proxies = get_settings().trusted_proxy_ips()
    if trusted_proxies and direct_peer in trusted_proxies:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

    return direct_peer


async def _resolve_api_key_identity(token: str, slug: str, request: Request) -> GatewayIdentity:
    resolved = await verify_gateway_api_key(token, slug)
    if resolved is None:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    allowed_ips = resolved["allowed_ips"]
    if allowed_ips and _client_ip(request) not in allowed_ips:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    return GatewayIdentity(
        company_id=resolved["company_id"], deployment_id=resolved["deployment_id"], auth_kind="api_key"
    )


async def _resolve_session_token_identity(token: str, slug: str) -> GatewayIdentity:
    try:
        claims = verify_session_token(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid_session_token") from exc

    # The token proves company_id/deployment_id at mint time; cross-check that the slug
    # the browser actually called still maps to that same deployment (PRD §7.5) - a
    # mismatch (deployment renamed/deleted, or a token replayed against a different
    # deployment's URL) is "as if the deployment did not exist", same as every other
    # gateway resolution failure.
    deployment_id = await resolve_deployment_id_by_slug(claims["company_id"], slug)
    if deployment_id is None or deployment_id != claims["deployment_id"]:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    return GatewayIdentity(
        company_id=claims["company_id"],
        deployment_id=deployment_id,
        auth_kind="session_token",
        external_user_id=claims.get("external_user_id"),
        claims=claims.get("claims") or {},
    )


async def require_gateway_api_key(
    slug: str, request: Request, authorization: Annotated[str | None, Header()] = None
) -> GatewayIdentity:
    """API-key-only auth (PRD §7.5: session tokens are themselves minted via an API key,
    so minting a session token can't itself be authorized by a session token)."""
    token = _extract_bearer(authorization)
    return await _resolve_api_key_identity(token, slug, request)


async def require_gateway_auth(
    slug: str, request: Request, authorization: Annotated[str | None, Header()] = None
) -> GatewayIdentity:
    """API key or session token (PRD §6.3/§7.5) - distinguished by the configured gateway
    key prefix (`ak_` by default), never by trying one and falling back to the other."""
    token = _extract_bearer(authorization)
    if token.startswith(get_settings().GATEWAY_API_KEY_PREFIX):
        return await _resolve_api_key_identity(token, slug, request)
    return await _resolve_session_token_identity(token, slug)


def _enforce_cors(identity: GatewayIdentity, deployment: DeploymentRow, request: Request, response: Response) -> None:
    """API-key calls are server-to-server (PRD §6.3: "server-to-server only") and skip
    this entirely. A session-token call only comes from a browser, so its `Origin` must
    be in the deployment's own `allowed_origins` - empty/unset means no browser origin is
    configured yet, so every browser-origin request is denied (deny-by-default, PRD §12).

    This validates the actual request, not a CORS preflight: a real cross-origin browser
    call also needs an `OPTIONS` responder answering before the browser ever sends this
    request, which isn't built yet (out of scope with the rest of the "full browser
    session-token experience" per the ai-gateway plan - same boundary as `streamDeployment`).
    """
    if identity.auth_kind != "session_token":
        return

    origin = request.headers.get("origin")
    if not origin or origin not in deployment.allowed_origins:
        raise HTTPException(status_code=403, detail="origin_not_allowed")

    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"


GatewayAuth = Annotated[GatewayIdentity, Depends(require_gateway_auth)]
ApiKeyAuth = Annotated[GatewayIdentity, Depends(require_gateway_api_key)]


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    return {"status": "ok"}


class RunRequestUser(BaseModel):
    external_id: str
    claims: dict = {}


class RunRequest(BaseModel):
    input: str
    conversation_id: str | None = None
    user: RunRequestUser | None = None

    @field_validator("conversation_id")
    @classmethod
    def _conversation_id_is_uuid(cls, value: str | None) -> str | None:
        if value is not None:
            UUID(value)
        return value


class RunResult(BaseModel):
    conversation_id: str | None = None
    output: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    trace_id: str


@app.post("/v1/deployments/{slug}/run", response_model=RunResult)
async def runDeployment(
    slug: str, body: RunRequest, identity: GatewayAuth, request: Request, response: Response
) -> Response:
    """Server-to-server (or session-token/browser) synchronous run (PRD §4.4-C).

    REAL: resolves the deployment, enforces CORS for session-token callers, runs via
    `app.services.runtime.run_deployment_agent` (real quota reservation/reconciliation,
    rate limiting, the deployment's own guardrail_overrides layered on the agent
    version's guardrails) - a session token's embedded `external_user_id`/`claims` always
    win over anything in the request body (PRD §7.5: "the gateway ignores identity in the
    body when a session token is used"); only a server-to-server API-key call may supply
    `body.user` at all.
    """
    deployment = await resolve_deployment(identity.company_id, identity.deployment_id)
    if deployment is None or not deployment.enabled:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    _enforce_cors(identity, deployment, request, response)

    if identity.auth_kind == "session_token":
        external_user_id = identity.external_user_id
    else:
        external_user_id = body.user.external_id if body.user else None

    try:
        result = await run_deployment_agent(
            identity.company_id,
            identity.deployment_id,
            _GATEWAY_RUN_ROLE,
            body.input,
            external_user_id=external_user_id,
            conversation_id=body.conversation_id,
        )
    except QuotaExceededError as exc:
        return JSONResponse(
            status_code=429,
            content={
                "error": "quota_exceeded",
                "reason": exc.reason,
                "reset_at": exc.reset_at.isoformat() if exc.reset_at else None,
            },
        )
    except Exception as exc:  # surface as a clear upstream error, not a 500 crash
        logger.exception("deployment run failed")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "detail": str(exc)})

    return RunResult(
        conversation_id=result["conversation_id"],
        output=result["output"],
        tokens_in=result.get("tokens_in"),
        tokens_out=result.get("tokens_out"),
        cost_usd=result.get("cost_usd"),
        latency_ms=result["latency_ms"],
        trace_id=result["trace_id"],
    )


@app.post("/v1/deployments/{slug}/stream")
async def streamDeployment(slug: str, body: dict) -> Response:
    """SSE run, from a server or a browser using a session token. Stub — out of scope
    with Playground's own `/stream` (also unimplemented)."""
    return _not_implemented("streamDeployment")


class SessionRequest(BaseModel):
    external_user_id: str | None = None
    claims: dict = {}


class SessionResponse(BaseModel):
    session_token: str
    expires_in: int


@app.post("/v1/deployments/{slug}/session", response_model=SessionResponse)
async def createDeploymentSession(slug: str, body: SessionRequest, identity: ApiKeyAuth) -> SessionResponse:
    """Issue a 5-minute browser session token (PRD §7.5). REAL: API-key-only auth, mints
    via `app.services.gateway_auth.mint_session_token` with the end-user identity the
    calling server vouches for embedded at mint time."""
    token, expires_in = mint_session_token(
        identity.company_id, identity.deployment_id, body.external_user_id or "", body.claims
    )
    return SessionResponse(session_token=token, expires_in=expires_in)


class DeploymentInfo(BaseModel):
    display_name: str
    role: str
    languages: list[str]


@app.get("/v1/deployments/{slug}/info", response_model=DeploymentInfo)
async def getDeploymentInfo(slug: str, identity: GatewayAuth, request: Request, response: Response) -> Response:
    """display_name, role, languages only - never instructions (PRD §6.1a). REAL: these
    come straight from the agent version's own `AgentIdentitySpec`, not the compiled
    `role` (system prompt) `compile_spec` builds for the Agent itself, which folds
    `instructions` in and would leak it."""
    deployment = await resolve_deployment(identity.company_id, identity.deployment_id)
    if deployment is None or not deployment.enabled:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    _enforce_cors(identity, deployment, request, response)

    version = await resolve_agent_version(identity.company_id, deployment.agent_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    spec_identity = AgentSpecDoc.model_validate(version.spec).identity
    return DeploymentInfo(
        display_name=spec_identity.display_name, role=spec_identity.role, languages=spec_identity.languages
    )


@app.post("/v1/deployments/{slug}/approvals/{id}")
async def decideApproval(slug: str, id: str, body: dict) -> Response:
    """Phase 2."""
    return _not_implemented("decideApproval")


@app.get("/.well-known/jwks.json")
async def getJwks() -> dict:
    """Public keys for verifying delegated identity tokens (PRD §7.5/7.6). REAL: ai-gateway's
    own RSA keypair (`app.services.gateway_auth`), not console-api's."""
    return get_jwks()
