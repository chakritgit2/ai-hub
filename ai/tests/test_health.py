from fastapi.testclient import TestClient


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
    assert resp.json() == {"keys": []}


def test_internal_stub_returns_501(runtime_client: TestClient) -> None:
    resp = runtime_client.post("/internal/v1/agents/recompile-all")
    assert resp.status_code == 501
    assert resp.json()["operation_id"] == "recompileAllAgents"
