"""ai-gateway FastAPI app.

Serves the public gateway API (PRD §9.4): `/v1/deployments/{slug}/*` (API
key / session token auth) and the unauthenticated JWKS endpoint used by tool
targets to verify delegated identity tokens (PRD §7.5/7.6).

Every route's function name matches the `operationId` in
`dynamiq-console/contracts/openapi/ai-public.yaml` exactly. Only `getJwks`
is a real, working implementation (an empty but correctly-shaped key set);
everything else is a 501 stub.
"""
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from app.core.otel import setup_tracing

app = FastAPI(title="ai-gateway")
setup_tracing(app)


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
    return {"status": "ok"}


@app.post("/v1/deployments/{slug}/run")
async def runDeployment(slug: str, body: dict) -> Response:
    """Server-to-server synchronous run (PRD §4.4-C). Stub."""
    return _not_implemented("runDeployment")


@app.post("/v1/deployments/{slug}/stream")
async def streamDeployment(slug: str, body: dict) -> Response:
    """SSE run, from a server or a browser using a session token. Stub."""
    return _not_implemented("streamDeployment")


@app.post("/v1/deployments/{slug}/session")
async def createDeploymentSession(slug: str, body: dict) -> Response:
    """Issue a 5-minute browser session token (PRD §7.5). Stub."""
    return _not_implemented("createDeploymentSession")


@app.get("/v1/deployments/{slug}/info")
async def getDeploymentInfo(slug: str) -> Response:
    """display_name, role, languages only - never instructions (PRD §6.1a). Stub."""
    return _not_implemented("getDeploymentInfo")


@app.post("/v1/deployments/{slug}/approvals/{id}")
async def decideApproval(slug: str, id: str, body: dict) -> Response:
    """Phase 2."""
    return _not_implemented("decideApproval")


@app.get("/.well-known/jwks.json")
async def getJwks() -> dict:
    """Public keys for verifying delegated identity tokens (PRD §7.5/7.6).

    REAL: correctly-shaped (empty) JWKS response - no signing key is
    provisioned in this skeleton yet (see GATEWAY_JWKS_PRIVATE_KEY_PATH in
    .env.example), so the key set is empty rather than fake/placeholder keys.
    """
    return {"keys": []}
