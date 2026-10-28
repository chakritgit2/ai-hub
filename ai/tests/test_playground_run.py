import asyncio
import json
import os
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from dynamiq.callbacks.streaming import AsyncStreamingIteratorCallbackHandler, BaseStreamingCallbackHandler
from dynamiq.nodes.agents.base import StreamChunk, StreamChunkChoice, StreamChunkChoiceDelta
from dynamiq.runnables.base import RunnableStatus
from dynamiq.types.cancellation import CancellationToken
from fastapi.testclient import TestClient

from app.core.db import get_company_session
from app.db.tables import guardrail_events_table, runs_table
from app.main_runtime import app as runtime_app
from app.main_runtime import require_runtime_auth
from app.services import compiler, run_registry
from app.services.agent_spec import GuardrailsSpec
from app.services.runtime import execute_agent_run, resolve_and_compile, run_deployment_agent, run_playground_agent
from app.services.streaming_events import extract_answer_delta as _extract_answer_delta

from .markers import fake_runtime_claims, requires_openai_key, requires_postgres


class _FakeAgent:
    """Stands in for `dynamiq.nodes.agents.Agent` so the run path can be
    exercised without an OpenAI key or network access."""

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(output={"content": "fake reply"}, status=RunnableStatus.SUCCESS)


@pytest.fixture(autouse=True)
def _reset_sse_app_status():
    """`sse_starlette.sse.AppStatus.should_exit_event` is a lazily-created `anyio.Event`
    cached as a class attribute for the life of the process, bound to whichever event loop
    was running the first time any `EventSourceResponse` ran - but this suite gives every
    async test its own fresh event loop (see conftest.py's `_fresh_engine`/`_fresh_redis`),
    so a second SSE-touching test reusing the already-bound Event blows up with "bound to a
    different event loop" during teardown. Resetting it before every test in this file
    (not just the streaming ones, for simplicity - a reset is a no-op for non-SSE tests)
    makes it lazily recreate itself on whichever loop is actually running."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    yield


@pytest.fixture
def fake_agent(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeAgent)
    # build_llm is called from app.services.compiler.build_agent now (resolution/compile
    # lives there), not directly from app.services.runtime - see compiler.py.
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))


class _FakeStreamingAgent:
    """Like `_FakeAgent`, but also pushes a couple of fake answer chunks through any
    streaming-capable callback present in `config.callbacks` before returning - simulates
    what dynamiq's real `Agent.run()` does internally (PRD Goal #6). Pushes the actual
    `StreamChunk(choices=[...delta...]).model_dump()` shape via `on_node_execute_stream`'s
    `chunk=` kwarg - the same wrapping `Agent.stream_response` (dynamiq/nodes/agents/
    base.py) always applies before a real chunk ever reaches a callback - rather than a
    shortcut `event=StreamingEventMessage(data=<plain str>)`, which no real dynamiq code
    path ever produces and would let a server-side string-shape bug slip past this test
    undetected (confirmed against the installed dynamiq==0.65.0 source)."""

    reply = "fake reply"

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
        if config is not None:
            for callback in config.callbacks:
                if isinstance(callback, BaseStreamingCallbackHandler):
                    for chunk_text in ("fake ", "reply"):
                        stream_chunk = StreamChunk(
                            choices=[
                                StreamChunkChoice(
                                    delta=StreamChunkChoiceDelta(content=chunk_text, source="test-agent", step="answer")
                                )
                            ]
                        )
                        callback.on_node_execute_stream(
                            {}, chunk=stream_chunk.model_dump(), run_id=config.run_id
                        )
        return SimpleNamespace(output={"content": type(self).reply}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def fake_streaming_agent(monkeypatch):
    import dynamiq.nodes.agents

    _FakeStreamingAgent.reply = "fake reply"
    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeStreamingAgent)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))
    return _FakeStreamingAgent


class _FakeAgentPushingApprovalRequired:
    """See test_gateway_stream.py's class of the same name - simulates a write-tool call
    mid-run pushing PRD §6.5's `approval_required` marker onto `stream_handler`, to test
    the SSE-emitting side (`runPlaygroundStream`'s event loop /
    `extract_approval_required_event`) without needing a real LLM to decide to invoke a
    real write tool (covered directly in test_safe_http_tool.py instead)."""

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
        if config is not None:
            for callback in config.callbacks:
                if isinstance(callback, BaseStreamingCallbackHandler):
                    callback.on_node_execute_stream(
                        {},
                        chunk={
                            "dynamiq_console_event": "approval_required",
                            "approval_id": "approval-1",
                            "tool": "create-order",
                        },
                        run_id=config.run_id,
                    )
        return SimpleNamespace(output={"content": "fake reply"}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def fake_agent_pushing_approval_required(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeAgentPushingApprovalRequired)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))


class _FakeCancellableAgent:
    """Polls `config.cancellation` the same way a real dynamiq node does via
    `check_cancellation` (dynamiq.types.cancellation, PRD Goal #6) - lets a test prove
    `POST /ai/v1/runs/{id}/cancel` (app.services.run_registry) actually reaches a run
    that's genuinely still "in progress" inside `asyncio.to_thread(agent.run, ...)`, not
    just one that already returned."""

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if config is not None and config.cancellation.is_canceled:
                return SimpleNamespace(output=None, status=RunnableStatus.CANCELED)
            time.sleep(0.01)
        return SimpleNamespace(output={"content": "fake reply"}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def fake_cancellable_agent(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeCancellableAgent)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))


async def _drain_with_timeout(
    handler: AsyncStreamingIteratorCallbackHandler, timeout: float = 5.0
) -> list[str]:
    """Drains a streaming handler's queue, bounded by `timeout` - a hang here means
    `execute_agent_run` failed to set `stream_handler.done_event` on some exit path (see
    its docstring), which would otherwise leave a real SSE client's `async for` waiting
    forever. Failing fast on a timeout turns that into a clear test failure instead of a
    stuck test suite. Reuses `app.services.streaming_events.extract_answer_delta` - the
    same extraction the real SSE endpoint applies - so a regression there fails here too,
    not just at the HTTP-layer test."""
    deltas: list[str] = []

    async def _drain() -> None:
        async for event in handler:
            delta = _extract_answer_delta(event.data)
            if delta:
                deltas.append(delta)

    await asyncio.wait_for(_drain(), timeout=timeout)
    return deltas


def _parse_sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    # sse-starlette writes CRLF line endings - normalize before splitting on the blank
    # line that separates events, or a multi-event body collapses into a single block.
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        event_type = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event_type is not None and data is not None:
            events.append((event_type, json.loads(data)))
    return events


@requires_openai_key
@requires_postgres
async def test_run_playground_agent_returns_real_output(company_ids, resolvable_agent_version) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id, secret=os.environ["OPENAI_API_KEY"])

    result = await run_playground_agent(
        "Say hi in 3 words.", agent_version_id=version_id, role="developer", company_id=company_id
    )

    assert result["output"]
    assert isinstance(result["output"], str)
    assert result["trace_id"]
    assert isinstance(result["latency_ms"], int)


def test_playground_run_requires_runtime_token() -> None:
    """Real auth, no override — a request with no Authorization header is 401,
    never reaching run_playground_agent (PRD §4.4-B/§7.6)."""
    resp = TestClient(runtime_app).post("/ai/v1/playground/run", json={"input": "hi"})
    assert resp.status_code == 401


def test_playground_run_route_wired(runtime_client) -> None:
    """Even with the runtime_client fixture's default claims (empty company_id, a
    zero-UUID agent_version_id that resolves to nothing), the route must be reachable and
    fail with a clear upstream error (502), never an import/wiring error (500)."""
    resp = runtime_client.post("/ai/v1/playground/run", json={"input": "hi"})
    assert resp.status_code == 502


def test_malformed_conversation_id_is_422(runtime_client) -> None:
    resp = runtime_client.post("/ai/v1/playground/run", json={"input": "hi", "conversation_id": "not-a-uuid"})
    assert resp.status_code == 422


@requires_postgres
async def test_conversation_id_not_echoed_without_memory(fake_agent, company_ids, resolvable_agent_version) -> None:
    """A resolvable agent version with no external_user_id in the request means memory
    is never used, so a client-sent conversation_id must not come back as if the
    conversation had been continued."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(
        company_id, agent_version_id=version_id
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            resp = await client.post(
                "/ai/v1/playground/run",
                json={"input": "hi", "conversation_id": "3f2b8c1e-0000-4000-8000-000000000000"},
            )
        assert resp.status_code == 200
        assert resp.json()["conversation_id"] is None
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


@requires_postgres
async def test_route_persists_conversation_id(fake_agent, company_ids, resolvable_agent_version) -> None:
    # httpx.AsyncClient rather than TestClient, so requests run on this test's event loop -
    # the same one the company_ids fixture tears down on.
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    body = {"input": "hi", "user": {"external_id": "route-memory-test-user"}}
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(
        company_id, agent_version_id=version_id
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            resp1 = await client.post("/ai/v1/playground/run", json=body)
            assert resp1.status_code == 200
            conversation_id = resp1.json()["conversation_id"]
            assert conversation_id

            resp2 = await client.post("/ai/v1/playground/run", json={**body, "conversation_id": conversation_id})
        assert resp2.status_code == 200
        assert resp2.json()["conversation_id"] == conversation_id
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


@requires_openai_key
@requires_postgres
async def test_agent_remembers_previous_turn(company_ids, resolvable_agent_version) -> None:
    """End-to-end with a real LLM: memory from turn one is visible on turn two (PRD §6.2)."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id, secret=os.environ["OPENAI_API_KEY"])

    first = await run_playground_agent(
        "My favorite color is teal. Just acknowledge briefly.",
        agent_version_id=version_id,
        role="developer",
        company_id=company_id,
        external_user_id="playground-memory-test-user",
    )
    second = await run_playground_agent(
        "What's my favorite color? Answer with just the color.",
        agent_version_id=version_id,
        role="developer",
        company_id=company_id,
        external_user_id="playground-memory-test-user",
        conversation_id=first["conversation_id"],
    )
    assert second["conversation_id"] == first["conversation_id"]
    assert "teal" in (second["output"] or "").lower()


@requires_postgres
async def test_unknown_agent_version_id_raises(company_ids) -> None:
    company_id = company_ids()

    with pytest.raises(ValueError, match="not found"):
        await run_playground_agent("hi", agent_version_id=str(uuid.uuid4()), role="developer", company_id=company_id)


@requires_postgres
async def test_cross_company_agent_version_id_is_indistinguishable_from_not_found(
    company_ids, resolvable_agent_version
) -> None:
    """A version belonging to another company must behave exactly like an unknown one
    (PRD §12) - not a different error that would reveal it exists."""
    owner_company_id = company_ids()
    other_company_id = company_ids()
    version_id = await resolvable_agent_version(owner_company_id)

    with pytest.raises(ValueError, match="not found"):
        await run_playground_agent("hi", agent_version_id=version_id, role="developer", company_id=other_company_id)


# ---------------------------------------------------------------------------
# SSE streaming (PRD Goal #6, POST /ai/v1/playground/stream)
# ---------------------------------------------------------------------------


@requires_postgres
async def test_execute_agent_run_streams_tokens_when_no_output_guardrails(
    fake_streaming_agent, company_ids, resolvable_agent_version
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    handler = AsyncStreamingIteratorCallbackHandler(loop=asyncio.get_event_loop())

    result = await run_playground_agent(
        "hi", agent_version_id=version_id, role="developer", company_id=company_id, stream_handler=handler
    )
    deltas = await _drain_with_timeout(handler)

    assert result["streamed_live"] is True
    assert "".join(deltas) == "fake reply"


@requires_postgres
async def test_execute_agent_run_does_not_stream_when_output_guardrails_present(
    fake_streaming_agent, company_ids, resolvable_agent_version
) -> None:
    """An agent version with an output guardrail configured must never let live,
    unguarded tokens reach `stream_handler` - the text can only be released as one chunk,
    after the guardrail check, by the caller (see runPlaygroundStream)."""
    company_id = company_ids()
    guardrails = {"output": [{"check_type": "regex_blocklist", "action": "block", "pattern": r"\bnever\b"}]}
    version_id = await resolvable_agent_version(company_id, guardrails=guardrails)
    handler = AsyncStreamingIteratorCallbackHandler(loop=asyncio.get_event_loop())

    result = await run_playground_agent(
        "hi", agent_version_id=version_id, role="developer", company_id=company_id, stream_handler=handler
    )
    deltas = await _drain_with_timeout(handler)

    assert result["streamed_live"] is False
    assert deltas == []


@requires_postgres
async def test_execute_agent_run_streams_live_with_masking_when_output_guardrail_is_chunk_safe(
    fake_streaming_agent, company_ids, resolvable_agent_version
) -> None:
    """The gateway-config-cache-adjacent fix: a chunk-safe, non-block output guardrail
    (mask/flag, not valid_json/valid_choices) must no longer fall back to the old
    "buffer everything, send one chunk" behavior - it should stream live with each chunk
    masked inline (PRD §6.4 "every chunk passes fast checks... before sending")."""
    company_id = company_ids()
    guardrails = {"output": [{"check_type": "regex_blocklist", "action": "mask", "pattern": r"reply"}]}
    version_id = await resolvable_agent_version(company_id, guardrails=guardrails)
    handler = AsyncStreamingIteratorCallbackHandler(loop=asyncio.get_event_loop())

    result = await run_playground_agent(
        "hi", agent_version_id=version_id, role="developer", company_id=company_id, stream_handler=handler
    )
    deltas = await _drain_with_timeout(handler)

    assert result["streamed_live"] is True
    # fake_streaming_agent pushes "fake " then "reply" as two separate chunks - masking
    # must apply per chunk, independently, not just once at the end.
    assert "".join(deltas) == "fake [REDACTED]"
    assert result["output"] == "fake [REDACTED]"
    assert any(event.check == "regex_blocklist" and event.action == "mask" for event in result["guardrail_events"])


@requires_postgres
async def test_execute_agent_run_logs_output_guardrail_events_even_when_the_run_later_fails(
    company_ids, resolvable_agent_version, monkeypatch
) -> None:
    """A real code-review finding: output-stage guardrail events from chunks that were
    already checked+masked+streamed to the client must still be logged even when the
    overall run fails afterwards (e.g. a late tool-call error) - before this fix, the
    masking relay's result was drained but discarded in the failure path, silently
    losing the audit trail (PRD §6.4) for a mask that the client already received."""
    import dynamiq.nodes.agents

    class _FailsAfterStreaming:
        def __init__(self, **kwargs) -> None:
            self.memory = kwargs.get("memory")

        def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
            if config is not None:
                for callback in config.callbacks:
                    if not hasattr(callback, "on_node_execute_stream"):
                        continue
                    for chunk_text in ("fake ", "reply"):
                        stream_chunk = StreamChunk(
                            choices=[
                                StreamChunkChoice(
                                    delta=StreamChunkChoiceDelta(
                                        content=chunk_text, source="test-agent", step="answer"
                                    )
                                )
                            ]
                        )
                        callback.on_node_execute_stream({}, chunk=stream_chunk.model_dump(), run_id=config.run_id)
            raise RuntimeError("late tool-call failure")

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FailsAfterStreaming)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))

    company_id = company_ids()
    guardrails = {"output": [{"check_type": "regex_blocklist", "action": "mask", "pattern": r"reply"}]}
    version_id = await resolvable_agent_version(company_id, guardrails=guardrails)
    handler = AsyncStreamingIteratorCallbackHandler(loop=asyncio.get_event_loop())

    with pytest.raises(RuntimeError, match="late tool-call failure"):
        await run_playground_agent(
            "hi", agent_version_id=version_id, role="developer", company_id=company_id, stream_handler=handler
        )
    await _drain_with_timeout(handler)

    async with get_company_session(company_id) as session:
        rows = (
            (
                await session.execute(
                    guardrail_events_table.select().where(guardrail_events_table.c.company_id == uuid.UUID(company_id))
                )
            )
            .mappings()
            .all()
        )

    assert any(row["stage"] == "output" and row["check"] == "regex_blocklist" for row in rows)


@requires_postgres
async def test_execute_agent_run_fails_closed_when_the_masking_relay_itself_fails(
    fake_streaming_agent, company_ids, resolvable_agent_version, monkeypatch
) -> None:
    """A real code-review finding: before this fix, a bug inside the masking relay
    itself (after the underlying agent run had already succeeded) propagated as a bare,
    unlogged exception instead of the same fail-closed handling every other failure
    gets - this proves it's now symmetric with the agent.run()-fails case."""
    import app.services.runtime as runtime_module

    real_run_checks = runtime_module.run_checks

    async def _boom_on_output_stage(*args, **kwargs):
        # Only the relay's output-stage call should fail - the input-stage check must
        # still run for real, or the agent would never even be invoked.
        if kwargs.get("stage") == "output":
            raise RuntimeError("simulated relay bug")
        return await real_run_checks(*args, **kwargs)

    # runtime.py calls `run_checks` via its own `from ... import run_checks` binding,
    # not `app.services.guardrails.run_checks` - must patch the name as runtime.py
    # itself looks it up, or this has no effect on `_relay_masked_output_stream`'s call.
    monkeypatch.setattr(runtime_module, "run_checks", _boom_on_output_stage)

    company_id = company_ids()
    guardrails = {"output": [{"check_type": "regex_blocklist", "action": "mask", "pattern": r"reply"}]}
    version_id = await resolvable_agent_version(company_id, guardrails=guardrails)
    handler = AsyncStreamingIteratorCallbackHandler(loop=asyncio.get_event_loop())

    with pytest.raises(RuntimeError, match="simulated relay bug"):
        await run_playground_agent(
            "hi", agent_version_id=version_id, role="developer", company_id=company_id, stream_handler=handler
        )

    async with get_company_session(company_id) as session:
        rows = (
            (
                await session.execute(
                    guardrail_events_table.select().where(guardrail_events_table.c.company_id == uuid.UUID(company_id))
                )
            )
            .mappings()
            .all()
        )

    # The relay itself crashed before producing any output events - must not raise a
    # second, different exception trying to log, and must not silently succeed either.
    assert rows == []


@requires_postgres
async def test_execute_agent_run_streamed_live_false_when_input_blocked(
    fake_streaming_agent, company_ids, resolvable_agent_version
) -> None:
    """The agent is never even built when input is blocked - `done_event` must still be
    set (no hang) even though `stream_handler` was never attached to any callback list."""
    company_id = company_ids()
    guardrails = {"input": [{"check_type": "max_length", "action": "block", "max_length": 5}]}
    version_id = await resolvable_agent_version(company_id, guardrails=guardrails)
    handler = AsyncStreamingIteratorCallbackHandler(loop=asyncio.get_event_loop())

    result = await run_playground_agent(
        "this input is way too long",
        agent_version_id=version_id,
        role="developer",
        company_id=company_id,
        stream_handler=handler,
    )
    deltas = await _drain_with_timeout(handler)

    assert result["streamed_live"] is False
    assert deltas == []


@requires_postgres
async def test_run_deployment_agent_unaffected_by_stream_handler_param(
    fake_agent, company_ids, resolvable_agent_version, make_deployment
) -> None:
    """`run_deployment_agent` has no `stream_handler` kwarg at all - a regression guard
    that adding live streaming to `execute_agent_run` never leaked into the
    deployment/quota path."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    deployment_id, _slug = make_deployment(company_id, version_id)

    result = await run_deployment_agent(company_id, deployment_id, "developer", "hi")

    assert result["output"] == "fake reply"
    assert result["streamed_live"] is False


def test_playground_stream_requires_runtime_token() -> None:
    """Real auth, no override - mirrors test_playground_run_requires_runtime_token."""
    resp = TestClient(runtime_app).post("/ai/v1/playground/stream", json={"input": "hi"})
    assert resp.status_code == 401


def test_malformed_conversation_id_is_422_on_stream(runtime_client) -> None:
    resp = runtime_client.post(
        "/ai/v1/playground/stream", json={"input": "hi", "conversation_id": "not-a-uuid"}
    )
    assert resp.status_code == 422


async def test_playground_stream_route_wired() -> None:
    """Even with default claims (empty company_id, a zero-UUID agent_version_id that
    resolves to nothing), the route must be reachable and fail with a clear `error` SSE
    event, never an import/wiring error (500) - the SSE-shaped counterpart to
    test_playground_run_route_wired's 502 check."""
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims("")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            resp = await client.post("/ai/v1/playground/stream", json={"input": "hi"})
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        assert events
        assert events[-1][0] == "error"
        assert events[-1][1]["error"] == "upstream_error"
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


@requires_postgres
async def test_playground_stream_emits_tokens_then_final(
    fake_streaming_agent, company_ids, resolvable_agent_version
) -> None:
    """Full route-level happy path - proves Goal #6's "stream answers token by token"
    wording actually holds at the HTTP layer: at least one `token` event arrives before
    exactly one terminal `final` event."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(
        company_id, agent_version_id=version_id
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            resp = await client.post("/ai/v1/playground/stream", json={"input": "hi"})
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)

        assert [e for e in events if e[0] == "error"] == []
        token_events = [e for e in events if e[0] == "token"]
        final_events = [e for e in events if e[0] == "final"]
        assert len(token_events) >= 1
        assert len(final_events) == 1
        assert events[-1] == final_events[0]
        assert "".join(e[1]["delta"] for e in token_events) == "fake reply"
        assert final_events[0][1]["output"] == "fake reply"
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


@requires_postgres
async def test_playground_stream_emits_approval_required_before_final(
    fake_agent_pushing_approval_required, company_ids, resolvable_agent_version
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(
        company_id, agent_version_id=version_id
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            resp = await client.post("/ai/v1/playground/stream", json={"input": "hi"})
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)

        approval_events = [e for e in events if e[0] == "approval_required"]
        assert len(approval_events) == 1
        assert approval_events[0][1] == {"approval_id": "approval-1", "tool": "create-order"}

        final_events = [e for e in events if e[0] == "final"]
        assert len(final_events) == 1
        assert events.index(approval_events[0]) < events.index(final_events[0])
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


@requires_postgres
async def test_playground_stream_emits_run_started_first(
    fake_streaming_agent, company_ids, resolvable_agent_version
) -> None:
    """The one and only way a client learns the id to later call
    POST /ai/v1/runs/{id}/cancel with (PRD Goal #6) - must arrive before any token."""
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(
        company_id, agent_version_id=version_id
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            resp = await client.post("/ai/v1/playground/stream", json={"input": "hi"})
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)

        assert events[0][0] == "run_started"
        assert uuid.UUID(events[0][1]["run_id"])
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)


# ---------------------------------------------------------------------------
# Cancellation (PRD Goal #6 "stop generating", POST /ai/v1/runs/{id}/cancel)
# ---------------------------------------------------------------------------


async def _cancel_once_registered(run_id: str, company_id: str, agent_version_id: str) -> None:
    """Polls `run_registry.request_cancel` until the target run has actually registered
    itself - right before `agent.run()` starts, well after `_execute_agent_run_body` has
    finished its own real resolve/compile/secret-decrypt work - rather than racing a fixed
    sleep against however long that happens to take."""
    for _ in range(300):
        if run_registry.request_cancel(run_id, company_id=company_id, agent_version_id=agent_version_id):
            return
        await asyncio.sleep(0.01)
    pytest.fail("run was never registered for cancellation")


@requires_postgres
async def test_execute_agent_run_returns_cancelled_when_signaled_mid_run(
    fake_cancellable_agent, company_ids, resolvable_agent_version
) -> None:
    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    run_id = str(uuid.uuid4())

    run_task = asyncio.create_task(
        run_playground_agent(
            "hi", agent_version_id=version_id, role="developer", company_id=company_id, run_id=run_id
        )
    )
    await _cancel_once_registered(run_id, company_id, version_id)
    result = await run_task

    assert result["cancelled"] is True
    assert result["output"] is None

    async with get_company_session(company_id) as session:
        row = (
            await session.execute(runs_table.select().where(runs_table.c.id == uuid.UUID(run_id)))
        ).mappings().one()
    assert row["status"] == "cancelled"


@requires_postgres
async def test_eval_source_runs_are_never_registered_for_external_cancellation(
    fake_agent, company_ids, resolvable_agent_version, monkeypatch
) -> None:
    """A real code-review finding: `run_registry` must only ever accept Playground-sourced
    runs - `app.services.eval_runner` calls `execute_agent_run` per dataset item against
    the same `agent_version_id` a Playground session for that company could hold a runtime
    token for, and eval runs have their own independent cancellation bookkeeping
    (`request_eval_run_cancellation`) - registering them here too would let a Playground
    token holder cancel an unrelated in-progress eval item just by guessing its run_id."""
    import app.services.runtime as runtime_module

    register_calls: list[tuple] = []
    monkeypatch.setattr(
        runtime_module.run_registry, "register", lambda *args, **kwargs: register_calls.append((args, kwargs))
    )

    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)

    await run_playground_agent("hi", agent_version_id=version_id, role="developer", company_id=company_id)
    assert len(register_calls) == 1

    register_calls.clear()
    compiled_definition = await resolve_and_compile(company_id, version_id, "developer")
    guardrails = GuardrailsSpec.model_validate(compiled_definition["guardrails"])
    await execute_agent_run(
        compiled_definition,
        guardrails,
        company_id=company_id,
        agent_version_id=version_id,
        source="eval",
        input_text="hi",
        external_user_id=None,
        conversation_id=None,
        deployment_id=None,
    )
    assert register_calls == []


def test_cancel_run_requires_runtime_token() -> None:
    resp = TestClient(runtime_app).post(f"/ai/v1/runs/{uuid.uuid4()}/cancel")
    assert resp.status_code == 401


def test_cancel_run_malformed_id_is_404(runtime_client, monkeypatch) -> None:
    import app.main_runtime as main_runtime_module

    monkeypatch.setattr(main_runtime_module, "CANCEL_REGISTRATION_GRACE_SECONDS", 0)
    resp = runtime_client.post("/ai/v1/runs/not-a-uuid/cancel")
    assert resp.status_code == 404


def test_cancel_run_unknown_id_is_404(runtime_client, monkeypatch) -> None:
    import app.main_runtime as main_runtime_module

    monkeypatch.setattr(main_runtime_module, "CANCEL_REGISTRATION_GRACE_SECONDS", 0)
    resp = runtime_client.post(f"/ai/v1/runs/{uuid.uuid4()}/cancel")
    assert resp.status_code == 404


def test_cancel_run_fails_fast_once_the_target_run_has_already_finished(runtime_client) -> None:
    """Regression: without run_registry.has_finished, a cancel call arriving just after
    its target run completed would block for the full CANCEL_REGISTRATION_GRACE_SECONDS
    before returning 404, unable to tell "already over" apart from "not registered yet"
    (found by code-review). Uses the real default grace window (not monkeypatched to 0)
    to prove the fast-fail path itself, not just that a shortened window is fast."""
    run_id = str(uuid.uuid4())
    run_registry.register(
        run_id, company_id="", agent_version_id="00000000-0000-0000-0000-000000000000", token=CancellationToken()
    )
    run_registry.unregister(run_id)

    started = time.monotonic()
    resp = runtime_client.post(f"/ai/v1/runs/{run_id}/cancel")
    elapsed = time.monotonic() - started

    assert resp.status_code == 404
    assert elapsed < 1.0


def test_cancel_run_succeeds_for_a_run_matching_the_token_claims(runtime_client) -> None:
    """Exercises the real route -> run_registry wiring directly, without needing a real
    in-flight agent run concurrently - test_execute_agent_run_returns_cancelled_when_
    signaled_mid_run above already proves the dynamiq-level cancellation itself works end
    to end. runtime_client's default claims (conftest.py) are company_id="",
    agent_version_id="00000000-0000-0000-0000-000000000000" - must match exactly."""
    run_id = str(uuid.uuid4())
    token = CancellationToken()
    run_registry.register(
        run_id, company_id="", agent_version_id="00000000-0000-0000-0000-000000000000", token=token
    )
    try:
        resp = runtime_client.post(f"/ai/v1/runs/{run_id}/cancel")
        assert resp.status_code == 200
        assert resp.json() == {"status": "cancel_requested"}
        assert token.is_canceled is True
    finally:
        run_registry.unregister(run_id)


def test_cancel_run_is_404_for_a_run_belonging_to_a_different_agent_version(runtime_client, monkeypatch) -> None:
    import app.main_runtime as main_runtime_module

    monkeypatch.setattr(main_runtime_module, "CANCEL_REGISTRATION_GRACE_SECONDS", 0)
    run_id = str(uuid.uuid4())
    token = CancellationToken()
    run_registry.register(run_id, company_id="", agent_version_id=str(uuid.uuid4()), token=token)
    try:
        resp = runtime_client.post(f"/ai/v1/runs/{run_id}/cancel")
        assert resp.status_code == 404
        assert token.is_canceled is False
    finally:
        run_registry.unregister(run_id)


@requires_postgres
async def test_playground_stream_ends_with_cancelled_event_when_the_run_is_signaled(
    fake_cancellable_agent, company_ids, resolvable_agent_version, monkeypatch
) -> None:
    """Route-level proof that `runPlaygroundStream` itself (not just `execute_agent_run`
    directly, covered above) turns a cancellation into a `cancelled` SSE event instead of
    `final`. The route's own `uuid4()` call is pinned to a known `run_id` so the test can
    target `_cancel_once_registered` at it deterministically, rather than scraping
    `run_registry`'s internals for "whatever just got registered" (flaky under full-suite
    load - a prior version of this test did that and was found to occasionally pick up an
    unrelated run). A real second, concurrent HTTP call to `POST /ai/v1/runs/{id}/cancel`
    is unnecessary here - `test_cancel_run_succeeds_for_a_run_matching_the_token_claims`
    above already proves that route reaches `run_registry` correctly; this test's job is
    only to prove `runPlaygroundStream` reacts to a cancelled run with a `cancelled` event
    instead of `final`."""
    import app.main_runtime as main_runtime_module

    company_id = company_ids()
    version_id = await resolvable_agent_version(company_id)
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(main_runtime_module, "uuid4", lambda: uuid.UUID(run_id))
    runtime_app.dependency_overrides[require_runtime_auth] = fake_runtime_claims(
        company_id, agent_version_id=version_id
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime_app), base_url="http://test") as client:
            request_task = asyncio.create_task(client.post("/ai/v1/playground/stream", json={"input": "hi"}))
            await _cancel_once_registered(run_id, company_id, version_id)
            resp = await request_task
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        assert events[-1][0] == "cancelled"
        assert events[-1][1]["run_id"] == run_id
    finally:
        runtime_app.dependency_overrides.pop(require_runtime_auth, None)
