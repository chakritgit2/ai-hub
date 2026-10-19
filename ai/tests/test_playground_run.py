import asyncio
import json
import os
import uuid
from types import SimpleNamespace

import httpx
import pytest
from dynamiq.callbacks.streaming import AsyncStreamingIteratorCallbackHandler, BaseStreamingCallbackHandler
from dynamiq.nodes.agents.base import StreamChunk, StreamChunkChoice, StreamChunkChoiceDelta
from dynamiq.runnables.base import RunnableStatus
from fastapi.testclient import TestClient

from app.main_runtime import _extract_answer_delta, app as runtime_app
from app.main_runtime import require_runtime_auth
from app.services import compiler
from app.services.runtime import run_deployment_agent, run_playground_agent

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


async def _drain_with_timeout(
    handler: AsyncStreamingIteratorCallbackHandler, timeout: float = 5.0
) -> list[str]:
    """Drains a streaming handler's queue, bounded by `timeout` - a hang here means
    `execute_agent_run` failed to set `stream_handler.done_event` on some exit path (see
    its docstring), which would otherwise leave a real SSE client's `async for` waiting
    forever. Failing fast on a timeout turns that into a clear test failure instead of a
    stuck test suite. Reuses `main_runtime._extract_answer_delta` - the same extraction
    the real SSE endpoint applies - so a regression there fails here too, not just at the
    HTTP-layer test."""
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
