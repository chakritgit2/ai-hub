"""ai-gateway FastAPI app.

Serves the public gateway API (PRD §9.4): `/v1/deployments/{slug}/*` (API
key / session token auth) and the unauthenticated JWKS endpoint used by tool
targets to verify delegated identity tokens (PRD §7.5/7.6).

Every route's function name matches the `operationId` in
`dynamiq-console/contracts/openapi/ai-public.yaml` exactly. REAL: `runDeployment`,
`streamDeployment`, `createDeploymentSession`, `getDeploymentInfo`, `getJwks` - API key /
session token auth (`require_gateway_auth`/`require_gateway_api_key`), real quota
reservation/reconciliation and rate limiting (`app.services.runtime.run_deployment_agent`),
CORS validation against a deployment's own `allowed_origins` for session-token/browser
callers. `streamDeployment` (PRD Goal #6, SSE) is real only for an agent-backed
deployment - a workflow-backed one still gets a 501 there (dynamiq's `GraphOrchestrator`
streaming behavior was never researched, unlike the plain `Agent` streaming this reuses,
and nobody has asked for it). `decideApproval` is real too - the app-side counterpart to
console-api's own `decideApproval` (PRD §6.5), both forwarding to the same
`app.services.approvals.decide_approval`. Nothing yet creates a `pending`
`runtime.tool_approvals` row (that requires hooking the write-tool call path itself,
deliberately deferred), so this is reachable today only against a row inserted by hand.

A deployment targets exactly one of a published agent version or a published workflow
version (PRD §6.3/§6.9 phase 4) - `runDeployment`/`getDeploymentInfo` branch on
`deployment.workflow_version_id` to call the workflow-shaped sibling of each agent
function (`app.services.workflow_runtime.run_deployment_workflow`/
`app.services.workflow_versions.resolve_workflow_identity`). Workflow runs have no
conversation memory, so `body.conversation_id` is rejected for a workflow-targeting
deployment rather than silently ignored.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

import jwt
from dynamiq.callbacks.streaming import AsyncStreamingIteratorCallbackHandler
from dynamiq.types.streaming import StreamingMode
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from app.core.auth import verify_gateway_api_key
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.otel import setup_tracing
from app.core.redis import get_redis
from app.services.agent_spec import AgentSpecDoc
from app.services.agent_versions import resolve_agent_version
from app.services.approvals import ApprovalAlreadyDecidedError, ApprovalNotFoundError, decide_approval
from app.services.deployments import DeploymentRow, resolve_deployment, resolve_deployment_id_by_slug
from app.services.gateway_auth import get_jwks, mint_session_token, verify_session_token
from app.services.quotas import QuotaExceededError
from app.services.runtime import attach_stream_completion, run_deployment_agent
from app.services.streaming_events import extract_answer_delta, extract_tool_step_event
from app.services.workflow_runtime import run_deployment_workflow
from app.services.workflow_versions import resolve_workflow_identity

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    yield
    # Same lru_cache-aware teardown ai/tests/conftest.py's own _fresh_engine/_fresh_redis
    # fixtures already use - only dispose what was actually created, so a process that
    # shut down without ever handling a request doesn't open a pool just to close it.
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
    if get_engine.cache_info().currsize:
        await get_engine().dispose()


app = FastAPI(title="ai-gateway", lifespan=_lifespan)
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
        identity_claims = identity.claims
    else:
        external_user_id = body.user.external_id if body.user else None
        identity_claims = body.user.claims if body.user else {}

    is_workflow = deployment.workflow_version_id is not None
    if is_workflow and body.conversation_id is not None:
        return JSONResponse(
            status_code=400,
            content={
                "error": "conversation_id_not_supported",
                "detail": "workflow deployments have no conversation memory",
            },
        )

    try:
        if is_workflow:
            result = await run_deployment_workflow(
                identity.company_id,
                identity.deployment_id,
                _GATEWAY_RUN_ROLE,
                body.input,
                external_user_id=external_user_id,
                claims=identity_claims,
            )
            result.setdefault("conversation_id", None)
        else:
            result = await run_deployment_agent(
                identity.company_id,
                identity.deployment_id,
                _GATEWAY_RUN_ROLE,
                body.input,
                external_user_id=external_user_id,
                conversation_id=body.conversation_id,
                claims=identity_claims,
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
    except (ValueError, RuntimeError) as exc:
        # The two exception types app.services.runtime documents itself as raising for a
        # genuine run failure (resolution/compile/secret errors, or the live agent run
        # itself failing) - surfaced as a clear upstream error, not a 500 crash. Anything
        # else is a bug in this gateway's own code (outside runtime's own try/except) and
        # must not be silently flattened into the same upstream_error bucket.
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
async def streamDeployment(slug: str, body: RunRequest, identity: GatewayAuth, request: Request) -> Response:
    """SSE run (PRD Goal #6), from a server (API key) or a browser using a session token.

    REAL for an agent-backed deployment: same `token`/`final`/`error` event shape as
    Playground's own `/ai/v1/playground/stream`, reusing
    `app.services.streaming_events.extract_answer_delta` so both apps parse dynamiq's
    stream chunks identically. **Additionally streams a `step` event** right before and
    right after each tool call - `{"phase": "tool_call", "tool": <name>}` then
    `{"phase": "tool_result", "tool": <name>, "sources"?: [...]}` - a PRD Goal #6
    follow-up scoped with the user to the gateway only: Playground stays on
    `StreamingMode.FINAL` unchanged (see `execute_agent_run`'s `stream_mode` docstring),
    only this endpoint passes `StreamingMode.ALL`. `sources` is only ever populated for a
    tool shaped like dynamiq's own TavilyTool/ExaTool output (see
    `extract_tool_step_event`) - never guessed for a generic `kind: http` tool, so a
    caller can never be shown a wrong/misleading "source" link.

    A workflow-backed deployment stays a 501 stub here - scoped out with the user:
    dynamiq's `GraphOrchestrator` streaming behavior was never researched (unlike the
    plain `Agent` streaming this reuses, which already went through that research for
    Playground), and nobody has asked for it. Use the synchronous `/run` above for a
    workflow deployment instead.

    CORS is enforced the same way, and at the same point, as `runDeployment`
    (`_enforce_cors`, before branching on workflow-vs-agent) - but applied to a throwaway
    `Response(status_code=204)` first and copied into the `EventSourceResponse` actually
    returned. Two things that each break this in a way only an in-process test transport
    would hide, both confirmed by reading Starlette's own `Response.init_headers` source:
    mutating the `response: Response` FastAPI would normally auto-merge only works when
    the endpoint returns a plain value, not when it returns its own `EventSourceResponse`
    directly (as every SSE endpoint must) - that bypasses the merge entirely, so CORS
    headers have to be copied in by hand; and the throwaway `Response` must be constructed
    with `status_code=204` specifically, never the default `200` with no body, which
    `init_headers` stamps with `Content-Length: 0` - copying that into the SSE response
    would declare a zero-length body to any real HTTP stack even though this keeps
    writing to it indefinitely (httpx's in-process `ASGITransport`, used by this file's
    own tests, ignores Content-Length framing entirely, so this would have silently
    shipped broken against every real server while every test still passed).

    `QuotaExceededError`/an upstream run failure can't become an HTTP 429/502 anymore
    once the SSE response has started (headers are already sent) - both become an
    `error` event instead, the same convention Playground's own `upstream_error`
    already uses. Deliberate, accepted tradeoff: a client must inspect the event stream,
    not just the HTTP status, to detect either failure mode on this endpoint.
    """
    deployment = await resolve_deployment(identity.company_id, identity.deployment_id)
    if deployment is None or not deployment.enabled:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    cors_response = Response(status_code=204)
    _enforce_cors(identity, deployment, request, cors_response)

    if deployment.workflow_version_id is not None:
        return _not_implemented("streamDeployment")

    if identity.auth_kind == "session_token":
        external_user_id = identity.external_user_id
        identity_claims = identity.claims
    else:
        external_user_id = body.user.external_id if body.user else None
        identity_claims = body.user.claims if body.user else {}

    stream_handler = AsyncStreamingIteratorCallbackHandler(loop=asyncio.get_running_loop())

    async def event_generator():
        run_task = asyncio.create_task(
            run_deployment_agent(
                identity.company_id,
                identity.deployment_id,
                _GATEWAY_RUN_ROLE,
                body.input,
                external_user_id=external_user_id,
                conversation_id=body.conversation_id,
                claims=identity_claims,
                stream_handler=stream_handler,
                stream_mode=StreamingMode.ALL,
            )
        )
        # See attach_stream_completion's own docstring (app.services.runtime) - required
        # beyond execute_agent_run's internal done_event for the same concurrent-drain
        # reasons as Playground's runPlaygroundStream, which already hit this live.
        attach_stream_completion(run_task, stream_handler)

        async for event in stream_handler:
            delta = extract_answer_delta(event.data)
            if delta:
                yield {"event": "token", "data": json.dumps({"delta": delta})}
                continue
            step_event = extract_tool_step_event(event.data)
            if step_event:
                yield {"event": "step", "data": json.dumps(step_event)}

        try:
            result = await run_task
        except QuotaExceededError as exc:
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "error": "quota_exceeded",
                        "reason": exc.reason,
                        "reset_at": exc.reset_at.isoformat() if exc.reset_at else None,
                    }
                ),
            }
            return
        except (ValueError, RuntimeError) as exc:
            # Same two exception types runDeployment's own 502 handler documents itself
            # as catching - anything else is a bug in this app's own code, not a run
            # failure, and must not be silently flattened into the same error event.
            logger.exception("deployment stream run failed")
            yield {"event": "error", "data": json.dumps({"error": "upstream_error", "detail": str(exc)})}
            return

        if not result.get("streamed_live") and result.get("output"):
            # Guardrail-gated or blocked-input path: nothing was streamed live above, so
            # the full (already final) text goes out as one token event before `final`.
            yield {"event": "token", "data": json.dumps({"delta": result["output"]})}

        yield {
            "event": "final",
            "data": json.dumps(
                {
                    "conversation_id": result.get("conversation_id"),
                    "output": result.get("output"),
                    "tokens_in": result.get("tokens_in"),
                    "tokens_out": result.get("tokens_out"),
                    "cost_usd": result.get("cost_usd"),
                    "latency_ms": result.get("latency_ms"),
                    "trace_id": result.get("trace_id"),
                }
            ),
        }

    return EventSourceResponse(event_generator(), headers=dict(cors_response.headers))


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
    """display_name, role, languages only - never instructions (PRD §6.1a). REAL: for an
    agent-backed deployment these come straight from the agent version's own
    `AgentIdentitySpec`, not the compiled `role` (system prompt) `compile_spec` builds for
    the Agent itself, which folds `instructions` in and would leak it. A workflow has no
    identity-shaped spec field at all, so a workflow-backed deployment reports its own
    `name` as `display_name` with a fixed `role: "workflow"` and no `languages`."""
    deployment = await resolve_deployment(identity.company_id, identity.deployment_id)
    if deployment is None or not deployment.enabled:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    _enforce_cors(identity, deployment, request, response)

    if deployment.workflow_version_id is not None:
        workflow_identity = await resolve_workflow_identity(identity.company_id, deployment.workflow_version_id)
        if workflow_identity is None:
            raise HTTPException(status_code=404, detail="deployment_not_found")
        return DeploymentInfo(display_name=workflow_identity.name, role="workflow", languages=[])

    version = await resolve_agent_version(identity.company_id, deployment.agent_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="deployment_not_found")

    spec_identity = AgentSpecDoc.model_validate(version.spec).identity
    return DeploymentInfo(
        display_name=spec_identity.display_name, role=spec_identity.role, languages=spec_identity.languages
    )


class DecideApprovalRequest(BaseModel):
    decision: Literal["approved", "rejected"]


class ApprovalResponse(BaseModel):
    id: str
    run_id: str
    tool_id: str
    request: dict
    status: str
    decided_by: str | None
    decided_at: str | None
    created_at: str


@app.post("/v1/deployments/{slug}/approvals/{id}", response_model=ApprovalResponse)
async def decideApproval(slug: str, id: str, body: DecideApprovalRequest, identity: GatewayAuth) -> Response:
    """Approve/reject a write tool call from the calling app (PRD §6.5) - same
    `decide_approval` service console-api's own `decideApproval` forwards to
    (ai-internal.yaml), reached here with ordinary API-key/session-token gateway auth
    instead of the internal JWT. `GatewayIdentity` carries no console user id, so
    `decided_by` records the session token's end user when there is one, falling back to a
    descriptor of the deployment/auth kind for a bare API-key call."""
    try:
        UUID(id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not_found")

    decided_by = identity.external_user_id or f"gateway:{identity.auth_kind}:{identity.deployment_id}"

    try:
        approval = await decide_approval(identity.company_id, id, body.decision, decided_by)
    except ApprovalNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")
    except ApprovalAlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=f"already_decided: {exc.status}")

    return JSONResponse(
        content=ApprovalResponse(
            id=approval.id,
            run_id=approval.run_id,
            tool_id=approval.tool_id,
            request=approval.request,
            status=approval.status,
            decided_by=approval.decided_by,
            decided_at=approval.decided_at.isoformat() if approval.decided_at else None,
            created_at=approval.created_at.isoformat(),
        ).model_dump()
    )


@app.get("/.well-known/jwks.json")
async def getJwks() -> dict:
    """Public keys for verifying delegated identity tokens (PRD §7.5/7.6). REAL: ai-gateway's
    own RSA keypair (`app.services.gateway_auth`), not console-api's."""
    return get_jwks()
