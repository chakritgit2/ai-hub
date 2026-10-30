"""Tests for the real CORS preflight (OPTIONS) responder (PRD Goal #6 "full browser
session-token experience", app.main_gateway._deploymentCorsPreflight). Uses the plain
sync `gateway_client` fixture with `resolve_deployment_cors` monkeypatched - no real
Postgres needed, since the whole point of this route is that it answers before any
company/deployment resolution through the normal authed path is even possible. The
service-level query itself (app.services.deployments.resolve_deployment_cors against the
new console.resolve_deployment_cors SQL function, db/migrations/post/
029_resolve_deployment_cors.sql) is covered by requires_postgres tests further below.
"""
import uuid

import pytest

import app.main_gateway as main_gateway_module
from app.services.deployments import DeploymentCors, resolve_deployment_cors

from .markers import requires_postgres


def _patch_cors(monkeypatch, result: DeploymentCors | None):
    async def _fake(_slug: str) -> DeploymentCors | None:
        return result

    monkeypatch.setattr(main_gateway_module, "resolve_deployment_cors", _fake)


def test_preflight_allows_a_configured_origin(gateway_client, monkeypatch) -> None:
    _patch_cors(monkeypatch, DeploymentCors(allowed_origins=["https://example.com"], enabled=True))

    resp = gateway_client.options("/v1/deployments/some-slug/run", headers={"Origin": "https://example.com"})

    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "https://example.com"
    assert resp.headers["access-control-allow-methods"] == "GET, POST, OPTIONS"
    assert "authorization" in resp.headers["access-control-allow-headers"].lower()


def test_preflight_denies_an_unlisted_origin_with_no_cors_headers(gateway_client, monkeypatch) -> None:
    """Deny-by-default (PRD §12), matching `_enforce_cors`'s own real-request behavior -
    204 either way (a preflight status code carries no meaning to the browser beyond
    2xx/non-2xx), but no `Access-Control-Allow-Origin` header is what actually blocks the
    browser from ever sending the real request."""
    _patch_cors(monkeypatch, DeploymentCors(allowed_origins=["https://example.com"], enabled=True))

    resp = gateway_client.options("/v1/deployments/some-slug/run", headers={"Origin": "https://evil.example"})

    assert resp.status_code == 204
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_denies_when_no_origin_header_sent(gateway_client, monkeypatch) -> None:
    _patch_cors(monkeypatch, DeploymentCors(allowed_origins=["https://example.com"], enabled=True))

    resp = gateway_client.options("/v1/deployments/some-slug/run")

    assert resp.status_code == 204
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_denies_for_a_disabled_deployment(gateway_client, monkeypatch) -> None:
    _patch_cors(monkeypatch, DeploymentCors(allowed_origins=["https://example.com"], enabled=False))

    resp = gateway_client.options("/v1/deployments/some-slug/run", headers={"Origin": "https://example.com"})

    assert resp.status_code == 204
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_denies_for_an_unknown_slug(gateway_client, monkeypatch) -> None:
    _patch_cors(monkeypatch, None)

    resp = gateway_client.options("/v1/deployments/does-not-exist/run", headers={"Origin": "https://example.com"})

    assert resp.status_code == 204
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.parametrize("path", ["/v1/deployments/some-slug/run", "/v1/deployments/some-slug/stream", "/v1/deployments/some-slug/info"])
def test_preflight_is_wired_on_every_session_token_reachable_route(gateway_client, monkeypatch, path) -> None:
    """The three routes `_enforce_cors` is actually called from (runDeployment/
    streamDeployment/getDeploymentInfo) must each have a real OPTIONS responder - a
    missing one would 405 (Starlette's default for an unregistered method), which blocks
    a real browser from ever sending the request this route exists to serve."""
    _patch_cors(monkeypatch, DeploymentCors(allowed_origins=["https://example.com"], enabled=True))

    resp = gateway_client.options(path, headers={"Origin": "https://example.com"})

    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "https://example.com"


def test_preflight_does_not_require_authorization(gateway_client, monkeypatch) -> None:
    """The crux of why this needs its own unauthenticated responder at all: a real
    browser's preflight never carries the Authorization header the normal routes need -
    confirm this route never 401s even with no Authorization header sent."""
    _patch_cors(monkeypatch, DeploymentCors(allowed_origins=["https://example.com"], enabled=True))

    resp = gateway_client.options("/v1/deployments/some-slug/run", headers={"Origin": "https://example.com"})

    assert resp.status_code != 401
    assert resp.status_code == 204


@requires_postgres
async def test_resolve_deployment_cors_reads_the_real_console_deployments_row(
    company_ids, make_company, resolvable_agent_version, make_deployment
) -> None:
    """Service-level test against the real `console.resolve_deployment_cors` SQL function
    (db/migrations/post/029_resolve_deployment_cors.sql) - requires that migration to have
    been applied to whatever Postgres `requires_postgres` points at."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    _deployment_id, slug = make_deployment(company_id, version_id, allowed_origins=["https://example.com"])

    cors = await resolve_deployment_cors(slug)

    assert cors is not None
    assert cors.enabled is True
    assert cors.allowed_origins == ["https://example.com"]


@requires_postgres
async def test_resolve_deployment_cors_returns_none_for_an_unknown_slug() -> None:
    cors = await resolve_deployment_cors(f"does-not-exist-{uuid.uuid4()}")

    assert cors is None
