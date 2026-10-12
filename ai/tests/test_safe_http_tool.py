import jwt
import pytest
from dynamiq.connections.connections import Http
from dynamiq.nodes.agents.exceptions import ToolExecutionException
from dynamiq.nodes.tools.http_api_call import HttpApiCallInputSchema

from app.integrations.safe_http_tool import SafeHttpApiCall
from app.services.gateway_auth import _load_or_create_key
from app.services.identity import DelegatedIdentityContext


def _make_tool(
    auth_mode: str = "service", audience: str | None = None, identity=None, secret: str | None = None
) -> SafeHttpApiCall:
    connection = Http(url="https://api.example.com/orders", method="GET", headers={})
    return SafeHttpApiCall(
        name="test-tool",
        description="",
        connection=connection,
        company_id="company-1",
        auth_mode=auth_mode,
        audience=audience,
        identity=identity,
        secret=secret,
    )


def test_delegated_tool_attaches_a_valid_signed_bearer_token() -> None:
    identity = DelegatedIdentityContext(sub="user-42", claims={"tier": "gold"}, act="deployment:dep-1", run_id="run-1")
    tool = _make_tool(auth_mode="delegated", audience="https://partner.example.com", identity=identity)

    kwargs = tool._safe_request_kwargs(HttpApiCallInputSchema())

    token = kwargs["headers"]["Authorization"].removeprefix("Bearer ")
    key = _load_or_create_key()
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], audience="https://partner.example.com")
    assert claims["company_id"] == "company-1"
    assert claims["sub"] == "user-42"
    assert claims["claims"] == {"tier": "gold"}
    assert claims["act"] == "deployment:dep-1"
    assert claims["run_id"] == "run-1"


def test_delegated_token_overrides_a_model_supplied_authorization_header() -> None:
    identity = DelegatedIdentityContext(sub="user-42", claims={}, act="act-1", run_id="run-1")
    tool = _make_tool(auth_mode="delegated", audience="https://partner.example.com", identity=identity)

    kwargs = tool._safe_request_kwargs(HttpApiCallInputSchema(headers={"Authorization": "Bearer attacker-supplied"}))

    assert kwargs["headers"]["Authorization"] != "Bearer attacker-supplied"
    token = kwargs["headers"]["Authorization"].removeprefix("Bearer ")
    key = _load_or_create_key()
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], audience="https://partner.example.com")
    assert claims["sub"] == "user-42"


def test_delegated_tool_without_identity_fails_closed() -> None:
    """A missing identity must never fall back to minting a blank-but-validly-signed
    token - that would be a silently anonymous call pretending to be attributable."""
    tool = _make_tool(auth_mode="delegated", audience="https://partner.example.com", identity=None)

    with pytest.raises(ToolExecutionException):
        tool._safe_request_kwargs(HttpApiCallInputSchema())


def test_service_mode_tool_has_no_authorization_header_attached() -> None:
    tool = _make_tool(auth_mode="service")

    kwargs = tool._safe_request_kwargs(HttpApiCallInputSchema())

    assert "Authorization" not in kwargs["headers"]


def test_service_mode_tool_with_secret_attaches_bearer_header() -> None:
    tool = _make_tool(auth_mode="service", secret="sk-service-secret")

    kwargs = tool._safe_request_kwargs(HttpApiCallInputSchema())

    assert kwargs["headers"]["Authorization"] == "Bearer sk-service-secret"


def test_service_mode_secret_overrides_a_model_supplied_authorization_header() -> None:
    tool = _make_tool(auth_mode="service", secret="sk-service-secret")

    kwargs = tool._safe_request_kwargs(HttpApiCallInputSchema(headers={"Authorization": "Bearer attacker-supplied"}))

    assert kwargs["headers"]["Authorization"] == "Bearer sk-service-secret"
