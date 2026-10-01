from fastapi.testclient import TestClient

from app.main_runtime import app as runtime_app


def test_runtime_healthz(runtime_client: TestClient) -> None:
    resp = runtime_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_runtime_readyz(runtime_client: TestClient) -> None:
    resp = runtime_client.get("/readyz")
    assert resp.status_code == 200


def test_gateway_healthz(gateway_client: TestClient) -> None:
    resp = gateway_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_gateway_readyz(gateway_client: TestClient) -> None:
    resp = gateway_client.get("/readyz")
    assert resp.status_code == 200


def test_gateway_jwks(gateway_client: TestClient) -> None:
    resp = gateway_client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["kty"] == "RSA"
    assert keys[0]["kid"]


def test_internal_stub_returns_501(runtime_client: TestClient) -> None:
    """runtime_client overrides require_internal_auth with fake claims (PRD §9.2) —
    past that gate, the route itself is still a 501 stub."""
    resp = runtime_client.post("/internal/v1/agents/recompile-all")
    assert resp.status_code == 501
    assert resp.json()["operation_id"] == "recompileAllAgents"


def test_internal_route_requires_internal_token() -> None:
    """Real auth, no override — PRD §9.2's internal-JWT gate is enforced even though
    the route past it is still a stub."""
    resp = TestClient(runtime_app).post(
        "/internal/v1/agents/recompile-all", headers={"X-Company-Id": "company-1"}
    )
    assert resp.status_code == 401


def test_internal_route_requires_company_id_header() -> None:
    resp = TestClient(runtime_app).post(
        "/internal/v1/agents/recompile-all", headers={"Authorization": "Bearer not-a-real-jwt"}
    )
    assert resp.status_code == 400
