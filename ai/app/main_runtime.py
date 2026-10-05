"""ai-runtime FastAPI app.

Serves two path groups from one process in this skeleton (production may
split `/internal/v1/*` onto its own port 8081 behind a NetworkPolicy per
PRD §9.2 - see the comment on `internal_router` below):

- `/ai/v1/*`      - Playground API (PRD §9.3), runtime-token auth.
- `/internal/v1/*` - console-api-only internal API (PRD §9.2), internal-JWT auth.

Every route's function name matches the `operationId` in
`dynamiq-console/contracts/openapi/ai-public.yaml` /
`ai-internal.yaml` exactly, so the contract can be traced 1:1 to this code.
Every route sits behind real auth (`require_runtime_auth` / `require_internal_auth`,
PRD §7.1/§7.6) - but `runPlayground` is the only one with real business logic past
that; everything else is still a 501 stub - this is a bootable skeleton, not a
feature-complete service.
"""
import asyncio
import logging
from typing import Annotated, Literal
from uuid import UUID

import httpx
import openai
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.core.auth import InvalidTokenError, verify_internal_token, verify_runtime_token
from app.core.crypto import encrypt_with_dek
from app.core.otel import setup_tracing
from app.integrations.dynamiq_adapter import build_llm
from app.integrations.safe_http_client import (
    EgressBlockedError,
    SafeHttpClient,
    SafeHttpClientError,
    parse_host_port,
)
from app.services.company_keys import get_or_create_company_dek
from app.services.compiler import compile_spec
from app.services.connection_secrets import decrypt_connection_secret, upsert_connection_secret
from app.services.connections import resolve_connection
from app.services.egress_allowlist import list_egress_allowlist
from app.services.runtime import run_playground_agent
from app.services.tools import resolve_tool

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


async def require_internal_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_company_id: Annotated[str | None, Header(alias="X-Company-Id")] = None,
) -> dict:
    """Verifies the 60-second console-api -> ai-runtime internal call token
    (PRD §9.2/§7.6, `aud=ai-internal`). `X-Company-Id` is required by every
    `/internal/v1/*` operation (ai-internal.yaml) but isn't itself part of the
    token's claims — RuntimeClient sends it as a separate header (PRD §7.6).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_authorization_header")
    if not x_company_id:
        raise HTTPException(status_code=400, detail="company_id_required")

    token = authorization.split(" ", 1)[1]
    try:
        claims = verify_internal_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return {**claims, "company_id": x_company_id}


def _not_implemented(operation_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"error": "not_implemented", "operation_id": operation_id},
    )


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


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


RuntimeAuth = Annotated[dict, Depends(require_runtime_auth)]


@app.post("/ai/v1/playground/run", response_model=RunResult)
async def runPlayground(
    body: RunRequest,
    claims: RuntimeAuth,
) -> RunResult:
    """Run a given agent version synchronously (PRD §4.4-B).

    REAL: builds and runs an actual Dynamiq Agent via
    `app.services.runtime.run_playground_agent`, resolving the token's own
    `agent_version_id`/`role` claims to the company's real agent spec, compiling it live
    and running it against the connection's real decrypted secret - with memory (PRD
    §6.2) and guardrails (PRD §6.4) both wired for real, and real runtime-token
    verification (`require_runtime_auth`) instead of trusting a client-supplied
    `X-Company-Id` header.
    """
    try:
        result = await run_playground_agent(
            body.input,
            agent_version_id=claims["agent_version_id"],
            role=claims["role"],
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
        tokens_in=result.get("tokens_in"),
        tokens_out=result.get("tokens_out"),
        cost_usd=result.get("cost_usd"),
        latency_ms=result["latency_ms"],
        trace_id=result["trace_id"],
    )


@app.post("/ai/v1/playground/stream")
async def runPlaygroundStream(body: RunRequest, claims: RuntimeAuth) -> Response:
    """Run a given agent version over SSE (PRD §6.2). Stub — auth is real."""
    return _not_implemented("runPlaygroundStream")


@app.post("/ai/v1/runs/{id}/cancel")
async def cancelRun(id: str, claims: RuntimeAuth) -> Response:
    return _not_implemented("cancelRun")


# --------------------------------------------------------------------------
# /internal/v1/* - console-api-only internal API (ai-internal.yaml)
# --------------------------------------------------------------------------
# Production topology serves this on a separate port (8081) reachable only
# from console-api pods via NetworkPolicy (PRD §9.2); this skeleton mounts
# it on the same app/port for simplicity of the boot check.


InternalAuth = Annotated[dict, Depends(require_internal_auth)]


class CompileRequest(BaseModel):
    spec: dict
    role: Literal["admin", "developer", "viewer"]


class CompileError(BaseModel):
    path: str
    message: str


class CompileResult(BaseModel):
    ok: bool
    compiled_definition: dict | None = None
    compiler_version: str | None = None
    dynamiq_version: str | None = None
    errors: list[CompileError] = []


@app.post("/internal/v1/agents/compile", response_model=CompileResult)
async def compileAgentSpec(body: CompileRequest, claims: InternalAuth) -> Response:
    """Validates + compiles an agent spec into a runnable Dynamiq definition (PRD §4.4-A,
    §6.1). REAL for Identity + Model (`app.services.compiler.compile_spec`) — Tools/
    Knowledge/Skills/Guardrails sections have no backing schema yet and are ignored.
    Compile failures (bad spec, cross-company connection, disallowed node type) are a
    normal 200 with `ok: false`, never a 4xx/5xx — only a genuine infra failure (e.g. DB
    unreachable) becomes the 502 below.
    """
    try:
        result = await compile_spec(body.spec, body.role, claims["company_id"])
    except Exception as exc:  # surface as a clear upstream error, not a 500 crash
        logger.exception("agent spec compilation failed")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "detail": str(exc)})
    return CompileResult(**result)


@app.post("/internal/v1/agents/recompile-all")
async def recompileAllAgents(claims: InternalAuth) -> Response:
    return _not_implemented("recompileAllAgents")


class ConnectionSecretInput(BaseModel):
    # ai-runtime, not console-api, owns secret storage (PRD §7.4/§7.7) - this must enforce
    # its own minimum independently of console-api's matching `strlen($secret) < 8` check
    # in ConnectionsController::putConnectionSecret, since this internal endpoint is
    # reachable by any caller holding a valid internal JWT, not just console-api. Without
    # this, an empty/too-short stored secret would later make testConnection silently fall
    # back to whatever OPENAI_API_KEY happens to be set in this process's environment
    # (OpenAIConnection's own default) instead of failing - reporting ok:true using a key
    # that has nothing to do with the company's actual connection.
    secret: str = Field(min_length=8)


class TestResult(BaseModel):
    ok: bool
    detail: str | None = None


@app.put("/internal/v1/connections/{id}/secret", status_code=204)
async def putConnectionSecret(id: str, body: ConnectionSecretInput, claims: InternalAuth) -> Response:
    """Envelope-encrypts `body.secret` with the company's own DEK and upserts it into
    `runtime.connection_secrets` (PRD §7.4/§7.7). A malformed or cross-company `id` is
    404, same "indistinguishable from not found" contract as `resolve_connection` itself
    — never an unhandled UUID parse error turning into a 500.
    """
    company_id = claims["company_id"]
    if not _is_uuid(id) or await resolve_connection(company_id, id) is None:
        raise HTTPException(status_code=404, detail="connection_not_found")

    try:
        company_dek = await get_or_create_company_dek(company_id)
        ciphertext, nonce = encrypt_with_dek(body.secret.encode(), company_dek.dek)
        await upsert_connection_secret(
            company_id=company_id,
            connection_id=id,
            ciphertext=ciphertext,
            nonce=nonce,
            dek_version=company_dek.dek_version,
        )
    except Exception as exc:  # surface as a clear upstream error, not a 500 crash
        logger.exception("storing connection secret failed")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "detail": str(exc)})
    return Response(status_code=204)


@app.post("/internal/v1/connections/{id}/test", response_model=TestResult)
async def testConnection(id: str, claims: InternalAuth) -> Response:
    """Decrypts the stored secret and makes one cheap real call to the provider
    (`client.models.list()`, no token cost) to prove it actually works. A connection that
    exists but has no secret stored yet, or whose provider type isn't wired for building,
    is a normal `ok: false` — never a 404/5xx, matching the compiler's
    "bad input is still 200 ok:false" pattern; only a genuine infra failure (DB
    unreachable, etc) becomes the 502 below.
    """
    company_id = claims["company_id"]
    if not _is_uuid(id):
        raise HTTPException(status_code=404, detail="connection_not_found")

    connection = await resolve_connection(company_id, id)
    if connection is None:
        raise HTTPException(status_code=404, detail="connection_not_found")

    if connection.api_base:
        try:
            host, port = parse_host_port(connection.api_base)
        except ValueError as exc:
            return TestResult(ok=False, detail=str(exc))

        allowlist = await list_egress_allowlist(company_id)
        try:
            await SafeHttpClient(allowlist).check_host(host, port)
        except EgressBlockedError as exc:
            return TestResult(ok=False, detail=f"egress_blocked: {exc.reason}")

    try:
        secret = await decrypt_connection_secret(company_id, id)
        if secret is None:
            return TestResult(ok=False, detail="no secret stored for this connection")

        _connection_obj, llm = build_llm(connection.type, {"api_key": secret, "url": connection.api_base})
    except ValueError as exc:
        return TestResult(ok=False, detail=str(exc))
    except Exception as exc:  # surface as a clear upstream error, not a 500 crash
        logger.exception("connection test setup failed")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "detail": str(exc)})

    try:
        await asyncio.to_thread(llm.client.models.list)
    except openai.AuthenticationError as exc:
        return TestResult(ok=False, detail=f"authentication failed: {exc}")
    except openai.APIError as exc:
        return TestResult(ok=False, detail=f"provider error: {exc}")

    return TestResult(ok=True)


@app.post("/internal/v1/tools/{id}/test", response_model=TestResult)
async def testTool(id: str, claims: InternalAuth) -> Response:
    """Proves a `kind: http` tool's configured URL is reachable past the egress allowlist
    (PRD §6.5/§7.4) — not an authenticated call: this slice has no tool-secret storage (unlike
    connections' `connection_secrets`), so no Authorization header is attached. `kind: builtin`/
    `kind: python` tools have no reachability story yet (builtin needs its own connection
    wiring; python has no execution path at all) and get an honest `ok: false` instead of a
    fake pass. Same "bad input is still 200 ok:false" pattern as `testConnection`.
    """
    company_id = claims["company_id"]
    if not _is_uuid(id):
        raise HTTPException(status_code=404, detail="tool_not_found")

    tool = await resolve_tool(company_id, id)
    if tool is None:
        raise HTTPException(status_code=404, detail="tool_not_found")

    if tool.kind != "http":
        return TestResult(ok=False, detail=f"test not supported for kind={tool.kind!r}")

    url = tool.config.get("url")
    if not url:
        return TestResult(ok=False, detail="tool config has no url")

    try:
        host, port = parse_host_port(url)
    except ValueError as exc:
        return TestResult(ok=False, detail=str(exc))

    allowlist = await list_egress_allowlist(company_id)
    client = SafeHttpClient(allowlist)
    try:
        await client.check_host(host, port)
    except EgressBlockedError as exc:
        return TestResult(ok=False, detail=f"egress_blocked: {exc.reason}")

    try:
        response = await client.request(
            tool.config.get("method", "GET"), url, headers=tool.config.get("headers") or {}
        )
    except EgressBlockedError as exc:
        return TestResult(ok=False, detail=f"egress_blocked: {exc.reason}")
    except SafeHttpClientError as exc:
        return TestResult(ok=False, detail=str(exc))
    except httpx.HTTPError as exc:
        return TestResult(ok=False, detail=f"request failed: {exc}")
    except (TypeError, ValueError) as exc:
        # tool.config came from a stored row, not this request - a malformed `headers`/
        # `method` (e.g. headers not a mapping) must not 500 any more than a bad url does.
        return TestResult(ok=False, detail=f"invalid tool config: {exc}")

    return TestResult(ok=response.status_code < 500, detail=f"status {response.status_code}")


@app.post("/internal/v1/kb/{id}/documents")
async def enqueueKbDocumentIndexing(id: str, body: dict, claims: InternalAuth) -> Response:
    """Phase 2."""
    return _not_implemented("enqueueKbDocumentIndexing")


@app.post("/internal/v1/kb/{id}/search")
async def searchKnowledgeBase(id: str, body: dict, claims: InternalAuth) -> Response:
    """Phase 2."""
    return _not_implemented("searchKnowledgeBase")


@app.get("/internal/v1/kb/{id}/export")
async def exportKnowledgeBase(id: str, claims: InternalAuth) -> Response:
    """Phase 2."""
    return _not_implemented("exportKnowledgeBase")


@app.post("/internal/v1/evals/estimate")
async def estimateEvalCost(body: dict, claims: InternalAuth) -> Response:
    """Phase 3."""
    return _not_implemented("estimateEvalCost")


@app.post("/internal/v1/evals")
async def createEvalRun(body: dict, claims: InternalAuth) -> Response:
    """Phase 3."""
    return _not_implemented("createEvalRun")


@app.post("/internal/v1/evals/{id}/cancel")
async def cancelEvalRun(id: str, claims: InternalAuth) -> Response:
    """Phase 3."""
    return _not_implemented("cancelEvalRun")


@app.post("/internal/v1/approvals/{id}")
async def decideApproval(id: str, body: dict, claims: InternalAuth) -> Response:
    """Phase 2."""
    return _not_implemented("decideApproval")
