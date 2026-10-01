"""ai-runtime FastAPI app.

Serves two path groups from one process in this skeleton (production may
split `/internal/v1/*` onto its own port 8081 behind a NetworkPolicy per
PRD §9.2 - see the comment on `internal_router` below):

- `/ai/v1/*`      - Playground API (PRD §9.3), runtime-token auth.
- `/internal/v1/*` - console-api-only internal API (PRD §9.2), internal-JWT auth.

Every route's function name matches the `operationId` in
`dynamiq-console/contracts/openapi/ai-public.yaml` /
`ai-internal.yaml` exactly, so the contract can be traced 1:1 to this code.
Only `runPlayground` is a real, working implementation; everything else is a
501 stub - this is a bootable skeleton, not a feature-complete service.
"""
import logging
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from app.core.auth import InvalidTokenError, verify_runtime_token
from app.core.otel import setup_tracing
from app.services.runtime import run_playground_agent

logger = logging.getLogger(__name__)

app = FastAPI(title="ai-runtime")
setup_tracing(app)


async def require_runtime_auth(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """Verifies the 5-minute Playground runtime token (PRD §4.4-B/§7.6).

    Returns the decoded claims (`company_id`, `user_id`, `role`,
    `agent_version_id`) so handlers use the token's company_id rather than a
    client-supplied header.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_authorization_header")

    token = authorization.split(" ", 1)[1]
    try:
        return verify_runtime_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _not_implemented(operation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"error": "not_implemented", "operation_id": operation_id},
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    # Real implementation should check DB/Redis connectivity before
    # reporting ready; kept trivially true here for the boot-check skeleton.
    return {"status": "ok"}


# --------------------------------------------------------------------------
# /ai/v1/* - Playground API (ai-public.yaml)
# --------------------------------------------------------------------------


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
        # Kept as `str` to match the contract (ai-public.yaml: type string), but a
        # malformed id must be a 422 here, not a ValueError surfacing as a 502 later.
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


@app.post("/ai/v1/playground/run", response_model=RunResult)
async def runPlayground(
    body: RunRequest,
    claims: Annotated[dict, Depends(require_runtime_auth)],
) -> RunResult:
    """Run a given agent version synchronously (PRD §4.4-B).

    REAL: builds and runs an actual Dynamiq Agent via
    `app.services.runtime.run_playground_agent`, with memory wired for real
    (PRD §6.2), and real runtime-token verification (`require_runtime_auth`)
    instead of trusting a client-supplied `X-Company-Id` header. In production
    this would resolve the agent version/connection from Postgres per-company;
    this skeleton always runs a single hardcoded OpenAI-backed agent — the
    token's `agent_version_id` claim isn't used to pick one yet.
    """
    try:
        result = await run_playground_agent(
            body.input,
            company_id=claims["company_id"],
            external_user_id=body.user.external_id if body.user else None,
            conversation_id=body.conversation_id,
        )
    except Exception as exc:  # surface as a clear upstream error, not a 500 crash
        logger.exception("playground run failed")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "detail": str(exc)})
    return RunResult(
        conversation_id=result["conversation_id"],
        output=result["output"],
        latency_ms=result["latency_ms"],
        trace_id=result["trace_id"],
    )


@app.post("/ai/v1/playground/stream")
async def runPlaygroundStream(
    body: RunRequest,
    claims: Annotated[dict, Depends(require_runtime_auth)],
) -> Response:
    """Run a given agent version over SSE (PRD §6.2). Stub — auth is real."""
    return _not_implemented("runPlaygroundStream")


@app.post("/ai/v1/runs/{id}/cancel")
async def cancelRun(id: str, claims: Annotated[dict, Depends(require_runtime_auth)]) -> Response:
    return _not_implemented("cancelRun")


# --------------------------------------------------------------------------
# /internal/v1/* - console-api-only internal API (ai-internal.yaml)
# --------------------------------------------------------------------------
# Production topology serves this on a separate port (8081) reachable only
# from console-api pods via NetworkPolicy (PRD §9.2); this skeleton mounts
# it on the same app/port for simplicity of the boot check.


@app.post("/internal/v1/agents/compile")
async def compileAgentSpec(body: dict) -> Response:
    return _not_implemented("compileAgentSpec")


@app.post("/internal/v1/agents/recompile-all")
async def recompileAllAgents() -> Response:
    return _not_implemented("recompileAllAgents")


@app.put("/internal/v1/connections/{id}/secret")
async def putConnectionSecret(id: str, body: dict) -> Response:
    return _not_implemented("putConnectionSecret")


@app.post("/internal/v1/connections/{id}/test")
async def testConnection(id: str) -> Response:
    return _not_implemented("testConnection")


@app.post("/internal/v1/kb/{id}/documents")
async def enqueueKbDocumentIndexing(id: str, body: dict) -> Response:
    """Phase 2."""
    return _not_implemented("enqueueKbDocumentIndexing")


@app.post("/internal/v1/kb/{id}/search")
async def searchKnowledgeBase(id: str, body: dict) -> Response:
    """Phase 2."""
    return _not_implemented("searchKnowledgeBase")


@app.get("/internal/v1/kb/{id}/export")
async def exportKnowledgeBase(id: str) -> Response:
    """Phase 2."""
    return _not_implemented("exportKnowledgeBase")


@app.post("/internal/v1/evals/estimate")
async def estimateEvalCost(body: dict) -> Response:
    """Phase 3."""
    return _not_implemented("estimateEvalCost")


@app.post("/internal/v1/evals")
async def createEvalRun(body: dict) -> Response:
    """Phase 3."""
    return _not_implemented("createEvalRun")


@app.post("/internal/v1/evals/{id}/cancel")
async def cancelEvalRun(id: str) -> Response:
    """Phase 3."""
    return _not_implemented("cancelEvalRun")


@app.post("/internal/v1/approvals/{id}")
async def decideApproval(id: str, body: dict) -> Response:
    """Phase 2."""
    return _not_implemented("decideApproval")
