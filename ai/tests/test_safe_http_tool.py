import asyncio
import uuid

import jwt
import pytest
import sqlalchemy as sa
from dynamiq.callbacks.streaming import AsyncStreamingIteratorCallbackHandler
from dynamiq.connections.connections import Http
from dynamiq.nodes.agents.exceptions import ToolExecutionException
from dynamiq.nodes.tools.http_api_call import HttpApiCallInputSchema

from app.core.config import get_settings
from app.core.db import get_company_session
from app.db.tables import tool_approvals_table
from app.integrations.safe_http_tool import SafeHttpApiCall
from app.services import approvals as approvals_module
from app.services.gateway_auth import _load_or_create_key
from app.services.identity import DelegatedIdentityContext

from .markers import requires_postgres


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


def _make_write_tool(company_id: str, **overrides) -> SafeHttpApiCall:
    connection = Http(url="https://api.example.com/orders", method="POST", headers={})
    fields = {
        "name": "write-tool",
        "description": "",
        "connection": connection,
        "company_id": company_id,
        "auth_mode": "service",
        "secret": "sk-should-never-leak-into-an-approval-row",
        "tool_id": str(uuid.uuid4()),
        "access_level": "write",
        "run_id": str(uuid.uuid4()),
        "allow_write_tools": True,
    }
    fields.update(overrides)
    return SafeHttpApiCall(**fields)


def _shrink_approval_timeout(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "APPROVAL_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(approvals_module, "_POLL_INTERVAL_SECONDS", 0.05)


@requires_postgres
async def test_execute_write_tool_gate_runs_before_the_request_is_ever_built(monkeypatch, company_ids) -> None:
    """PRD §6.5/§8.1 end-to-end through the real `execute()` call, not just
    `enforce_write_tool_gate` in isolation (test_approvals.py) - proves the ordering
    `SafeHttpApiCall._enforce_write_gate`'s docstring requires: the gate must run before
    `_safe_request_kwargs` ever attaches the service-mode secret, or a secret could leak
    into the `runtime.tool_approvals.request` JSONB a company's own console users can
    read. Times out (no human decides) rather than being approved/rejected - cheapest way
    to drive this through a real pending row without a concurrent decider."""
    _shrink_approval_timeout(monkeypatch)

    def _fail_if_called(self, input_data):
        raise AssertionError("_safe_request_kwargs must not run before the write-tool gate clears")

    monkeypatch.setattr(SafeHttpApiCall, "_safe_request_kwargs", _fail_if_called)
    tool = _make_write_tool(company_ids(), loop=asyncio.get_running_loop())

    with pytest.raises(ToolExecutionException, match="timed out waiting for human approval"):
        await asyncio.to_thread(tool.execute, HttpApiCallInputSchema(body={"amount": 100}))

    # The gate really did create a pending (now expired) row for this exact call, and -
    # the actual point of this test - its stored request never carries the secret.
    async with get_company_session(tool.company_id) as session:
        row = (
            await session.execute(
                sa.select(tool_approvals_table).where(
                    tool_approvals_table.c.tool_id == uuid.UUID(tool.tool_id)
                )
            )
        ).mappings().one()
    assert row["status"] == "expired"
    assert "sk-should-never-leak-into-an-approval-row" not in str(row["request"])


@requires_postgres
async def test_execute_async_write_tool_gate_runs_before_the_request_is_ever_built(monkeypatch, company_ids) -> None:
    _shrink_approval_timeout(monkeypatch)

    def _fail_if_called(self, input_data):
        raise AssertionError("_safe_request_kwargs must not run before the write-tool gate clears")

    monkeypatch.setattr(SafeHttpApiCall, "_safe_request_kwargs", _fail_if_called)
    tool = _make_write_tool(company_ids())

    with pytest.raises(ToolExecutionException, match="timed out waiting for human approval"):
        await tool.execute_async(HttpApiCallInputSchema(body={"amount": 100}))


@requires_postgres
async def test_execute_async_pushes_approval_required_onto_the_stream_handler(monkeypatch, company_ids) -> None:
    """PRD §6.5's SSE `approval_required` event - pushed the instant the `pending` row
    exists, via `enforce_write_tool_gate_or_raise`'s `_notify_pending` closure. Drains the
    real `AsyncStreamingIteratorCallbackHandler` queue to confirm the exact shape
    `app.services.streaming_events.extract_approval_required_event` expects lands there."""
    _shrink_approval_timeout(monkeypatch)
    stream_handler = AsyncStreamingIteratorCallbackHandler(loop=asyncio.get_running_loop())
    tool = _make_write_tool(company_ids(), stream_handler=stream_handler)

    with pytest.raises(ToolExecutionException, match="timed out waiting for human approval"):
        await tool.execute_async(HttpApiCallInputSchema(body={"amount": 100}))

    event = await asyncio.wait_for(stream_handler.queue.get(), timeout=1)
    assert event.data["dynamiq_console_event"] == "approval_required"
    assert event.data["tool"] == tool.name
    assert isinstance(event.data["approval_id"], str) and event.data["approval_id"]


@requires_postgres
async def test_execute_async_pushes_nothing_when_no_stream_handler_is_set(monkeypatch, company_ids) -> None:
    """The run isn't being streamed at all (`stream_handler=None`, the default/non-
    streaming case) - `_notify_pending` must be a silent no-op, not crash."""
    _shrink_approval_timeout(monkeypatch)
    tool = _make_write_tool(company_ids())
    assert tool.stream_handler is None

    with pytest.raises(ToolExecutionException, match="timed out waiting for human approval"):
        await tool.execute_async(HttpApiCallInputSchema(body={"amount": 100}))


@requires_postgres
async def test_execute_read_tool_never_touches_the_write_gate(monkeypatch, company_ids) -> None:
    """access_level == "read" (the common case) must skip the gate entirely - checked
    through the real `execute_async()` entry point, not `_safe_request_kwargs` directly
    (which never calls the gate itself either way - `execute`/`execute_async` do, only
    when access_level == "write"). A regression here would make every ordinary tool call
    block for up to 5 minutes."""
    import app.integrations.write_tool_gate as write_tool_gate_module

    called = False

    async def _record_call(*_args, **_kwargs):
        nonlocal called
        called = True

    # write_tool_gate.py bound its own `enforce_write_tool_gate` name at import time (`from
    # app.services.approvals import ... enforce_write_tool_gate`) - patching
    # app.services.approvals's own attribute wouldn't reach that already-bound reference.
    monkeypatch.setattr(write_tool_gate_module, "enforce_write_tool_gate", _record_call)
    tool = _make_write_tool(company_ids(), access_level="read")

    with pytest.raises(ToolExecutionException, match="Request blocked"):
        # No egress-allowlist entry exists for this ad-hoc company, so the real request
        # this read tool attempts is blocked by SafeHttpClient - a different,
        # unambiguously-not-approval-related failure, proving the gate was never reached.
        await tool.execute_async(HttpApiCallInputSchema())

    assert called is False
