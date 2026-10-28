import json
from types import SimpleNamespace

import httpx
import pytest
from dynamiq.nodes.agents.base import StreamChunk, StreamChunkChoice, StreamChunkChoiceDelta
from dynamiq.runnables.base import RunnableStatus
from fastapi.testclient import TestClient

from app.main_gateway import app as gateway_app
from app.services import compiler
from app.services.gateway_auth import mint_session_token

from .markers import requires_postgres, requires_redis


@pytest.fixture(autouse=True)
def _reset_sse_app_status():
    """See test_playground_run.py's fixture of the same name - `AppStatus.should_exit_event`
    is a lazily-created `anyio.Event` cached as a class attribute bound to whichever event
    loop first ran an `EventSourceResponse`; this suite gives every async test its own
    fresh loop, so a second SSE test reusing the already-bound Event blows up with "bound
    to a different event loop" during teardown. Reset before every test in this file."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    yield


class _FakeStreamingAgent:
    """Like test_playground_run.py's `_FakeStreamingAgent`, but also fires one
    `step="reasoning"` live-thought chunk (no `tool` key - must be ignored) and one
    structured pre-tool-call `step="reasoning"` chunk plus a matching `step="tool"`
    result chunk, shaped exactly like the real dynamiq `AgentReasoningEventMessageData`/
    `AgentToolResultEventMessageData` payloads (confirmed against the installed
    dynamiq==0.65.0 source, not guessed) - proves `streamDeployment`'s tool-call `step`
    SSE events end to end, not just its reused `token`/`final` plumbing."""

    reply = "fake gateway reply"

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
        if config is not None:
            for callback in config.callbacks:
                if not hasattr(callback, "on_node_execute_stream"):
                    continue
                for delta_kwargs in (
                    {"content": {"thought": "thinking about it"}, "source": "test-agent", "step": "reasoning"},
                    {
                        "content": {
                            "tool_run_id": "run-1",
                            "thought": "I should search the web",
                            "action": "tavily-search",
                            "tool": {"name": "tavily-search", "type": "x.TavilyTool", "action_type": "web_search"},
                            "action_input": {"query": "test query"},
                            "loop_num": 1,
                        },
                        "source": "tavily-search",
                        "step": "reasoning",
                    },
                    {
                        "content": {
                            "tool_run_id": "run-1",
                            "name": "tavily-search",
                            "tool": {"name": "tavily-search", "type": "x.TavilyTool", "action_type": "web_search"},
                            "input": {"query": "test query"},
                            "result": {
                                "content": {
                                    "sources_with_url": [
                                        "[Title A](https://a.example.com)",
                                        "[Title B](https://b.example.com) (favicon: https://b.example.com/favicon.ico)",
                                    ]
                                }
                            },
                            "files": [],
                            "loop_num": 1,
                            "output": {},
                            "timestamp": "2026-01-01T00:00:00+00:00",
                            "status": "success",
                        },
                        "source": "tavily-search",
                        "step": "tool",
                    },
                    {"content": "fake ", "source": "test-agent", "step": "answer"},
                    {"content": "gateway reply", "source": "test-agent", "step": "answer"},
                ):
                    stream_chunk = StreamChunk(choices=[StreamChunkChoice(delta=StreamChunkChoiceDelta(**delta_kwargs))])
                    callback.on_node_execute_stream({}, chunk=stream_chunk.model_dump(), run_id=config.run_id)
        return SimpleNamespace(output={"content": type(self).reply}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def fake_streaming_agent(monkeypatch):
    import dynamiq.nodes.agents

    _FakeStreamingAgent.reply = "fake gateway reply"
    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeStreamingAgent)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))
    return _FakeStreamingAgent


class _FakeAgentPushingApprovalRequired:
    """Simulates what a real `access_level: write` tool call does mid-run (push PRD
    §6.5's `approval_required` marker onto `stream_handler` via `send_to_queue`,
    `app.integrations.write_tool_gate.enforce_write_tool_gate_or_raise`'s
    `_notify_pending`) - without needing a real LLM to actually decide to invoke a write
    tool (that path is covered directly, with a real `SafeHttpApiCall`, by
    test_safe_http_tool.py). Only the SSE-emitting side - `streamDeployment`'s event loop
    turning this queued marker into a real `approval_required` SSE line via
    `extract_approval_required_event` - is what this test exercises."""

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
        if config is not None:
            for callback in config.callbacks:
                if not hasattr(callback, "on_node_execute_stream"):
                    continue
                callback.on_node_execute_stream(
                    {},
                    chunk={
                        "dynamiq_console_event": "approval_required",
                        "approval_id": "approval-1",
                        "tool": "create-order",
                    },
                    run_id=config.run_id,
                )
        return SimpleNamespace(output={"content": "fake gateway reply"}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def fake_agent_pushing_approval_required(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeAgentPushingApprovalRequired)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))


@pytest.fixture
async def gateway_async_client():
    """Same event-loop-sharing fix test_gateway_run.py's own fixture of the same name
    documents - a DB-backed test's HTTP calls must run on the same loop its async
    fixtures (company_ids, make_deployment, ...) used."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app), base_url="http://test") as client:
        yield client


def _parse_sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    # sse-starlette writes CRLF line endings - normalize before splitting on the blank
    # line that separates events, or a multi-event body collapses into one block.
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


def test_stream_requires_authorization_header() -> None:
    resp = TestClient(gateway_app).post("/v1/deployments/some-slug/stream", json={"input": "hi"})
    assert resp.status_code == 401


def test_stream_with_unknown_api_key_is_404() -> None:
    resp = TestClient(gateway_app).post(
        "/v1/deployments/some-slug/stream",
        json={"input": "hi"},
        headers={"Authorization": "Bearer ak_test_doesnotexist"},
    )
    assert resp.status_code == 404


@requires_postgres
async def test_stream_malformed_conversation_id_is_422(
    gateway_async_client, company_ids, make_company, resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream",
        json={"input": "hi", "conversation_id": "not-a-uuid"},
        headers={"Authorization": f"Bearer {full_key}"},
    )
    assert resp.status_code == 422


@requires_postgres
async def test_stream_workflow_deployment_is_not_implemented(
    gateway_async_client, company_ids, make_company, make_workflow_version, make_deployment, make_api_key
) -> None:
    """Scoped out with the user: dynamiq's GraphOrchestrator streaming behavior was never
    researched, unlike the plain Agent streaming this endpoint reuses for an
    agent-backed deployment."""
    company_id = company_ids()
    make_company(company_id)
    workflow_version_id = make_workflow_version(company_id, {})
    deployment_id, slug = make_deployment(company_id, workflow_version_id=workflow_version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 501
    assert resp.json()["error"] == "not_implemented"


@requires_postgres
async def test_stream_happy_path_emits_tokens_then_final(
    fake_streaming_agent, gateway_async_client, company_ids, make_company, resolvable_agent_version, make_deployment,
    make_api_key
) -> None:
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    assert [e for e in events if e[0] == "error"] == []

    token_events = [e for e in events if e[0] == "token"]
    final_events = [e for e in events if e[0] == "final"]
    assert len(final_events) == 1
    assert events[-1] == final_events[0]
    assert "".join(e[1]["delta"] for e in token_events) == "fake gateway reply"
    assert final_events[0][1]["output"] == "fake gateway reply"
    # Regression test: a Content-Length header copied from the throwaway CORS-headers
    # Response would declare a zero-length body to any real HTTP stack even though this
    # response keeps streaming - only an in-process test transport like this one would
    # ever let that slip through unnoticed, so it's asserted explicitly here.
    assert "content-length" not in resp.headers


@requires_postgres
async def test_stream_workflow_deployment_rejects_disallowed_origin_before_not_implemented(
    gateway_async_client, company_ids, make_company, make_workflow_version, make_deployment
) -> None:
    """CORS must be enforced before branching on workflow-vs-agent, same order
    runDeployment already uses - otherwise a disallowed-origin browser call would learn a
    workflow-backed deployment exists (via a 501 body) without ever passing the CORS
    check every other response is gated behind."""
    company_id = company_ids()
    make_company(company_id)
    workflow_version_id = make_workflow_version(company_id, {})
    deployment_id, slug = make_deployment(
        company_id, workflow_version_id=workflow_version_id, allowed_origins=["https://example.com"]
    )
    session_token, _ = mint_session_token(company_id, deployment_id, "user-1")

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {session_token}", "Origin": "https://evil.example"},
    )

    assert resp.status_code == 403


@requires_postgres
async def test_stream_emits_tool_call_and_tool_result_step_events_with_sources(
    fake_streaming_agent, gateway_async_client, company_ids, make_company, resolvable_agent_version, make_deployment,
    make_api_key
) -> None:
    """The structured pre-tool-call reasoning event becomes a `tool_call` step; the
    matching tool-result event becomes a `tool_result` step with `sources` parsed from
    the Tavily-shaped `sources_with_url` markdown links - PRD Goal #6 follow-up, gateway
    only (Playground stays on StreamingMode.FINAL, unaffected)."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    step_events = [e for e in events if e[0] == "step"]
    assert len(step_events) == 2

    tool_call = step_events[0][1]
    assert tool_call == {"phase": "tool_call", "tool": "tavily-search"}

    tool_result = step_events[1][1]
    assert tool_result["phase"] == "tool_result"
    assert tool_result["tool"] == "tavily-search"
    assert tool_result["sources"] == [
        {"title": "Title A", "url": "https://a.example.com"},
        {"title": "Title B", "url": "https://b.example.com"},
    ]

    # The live {"thought": "..."} token chunk shares step="reasoning" but has no "tool"
    # key - must never produce a step event of its own.
    assert step_events[0][1] != {"thought": "thinking about it"}


@requires_postgres
async def test_stream_rejects_session_token_from_disallowed_origin(
    fake_streaming_agent, gateway_async_client, company_ids, make_company, resolvable_agent_version, make_deployment
) -> None:
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, allowed_origins=["https://example.com"])
    session_token, _ = mint_session_token(company_id, deployment_id, "user-1")

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {session_token}", "Origin": "https://evil.example"},
    )

    assert resp.status_code == 403


@requires_postgres
async def test_stream_allows_session_token_from_allowed_origin(
    fake_streaming_agent, gateway_async_client, company_ids, make_company, resolvable_agent_version, make_deployment
) -> None:
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, allowed_origins=["https://example.com"])
    session_token, _ = mint_session_token(company_id, deployment_id, "user-1")

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {session_token}", "Origin": "https://example.com"},
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://example.com"
    events = _parse_sse_events(resp.text)
    assert [e for e in events if e[0] == "error"] == []
    assert [e for e in events if e[0] == "final"]


@requires_postgres
@requires_redis
async def test_stream_emits_quota_exceeded_error_event_when_rate_limited(
    fake_streaming_agent, gateway_async_client, company_ids, make_company, resolvable_agent_version, make_deployment,
    make_api_key
) -> None:
    """QuotaExceededError can't become an HTTP 429 anymore once the SSE response has
    started - it surfaces as an `error` event instead (see streamDeployment's own
    docstring for why)."""
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id, rate_limit_per_min=1)
    _key_id, full_key = make_api_key(company_id, [deployment_id])
    headers = {"Authorization": f"Bearer {full_key}"}

    first = await gateway_async_client.post(f"/v1/deployments/{slug}/stream", json={"input": "hi"}, headers=headers)
    assert first.status_code == 200
    assert [e for e in _parse_sse_events(first.text) if e[0] == "error"] == []

    second = await gateway_async_client.post(f"/v1/deployments/{slug}/stream", json={"input": "hi"}, headers=headers)
    assert second.status_code == 200  # headers already sent for a 200 SSE response
    events = _parse_sse_events(second.text)
    assert len(events) == 1
    assert events[0][0] == "error"
    assert events[0][1]["error"] == "quota_exceeded"
    assert events[0][1]["reason"] == "rate_limit"


@requires_postgres
async def test_stream_emits_approval_required_before_final(
    fake_agent_pushing_approval_required, gateway_async_client, company_ids, make_company, resolvable_agent_version,
    make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    make_company(company_id)
    version_id = await resolvable_agent_version(company_id)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    approval_events = [e for e in events if e[0] == "approval_required"]
    assert len(approval_events) == 1
    assert approval_events[0][1] == {"approval_id": "approval-1", "tool": "create-order"}

    final_events = [e for e in events if e[0] == "final"]
    assert len(final_events) == 1
    assert events.index(approval_events[0]) < events.index(final_events[0])


class _FakeStreamingAgentPushingApprovalRequired:
    """Like `_FakeAgentPushingApprovalRequired` above, but also streams real answer
    chunks *before* pushing the approval marker - onto whatever handler is actually in
    `config.callbacks`, which is `raw_handler` (not the outer `stream_handler`) whenever
    an output guardrail is configured and chunk-streamable (see runtime.py's
    `tool_stream_handler`). Exercises the exact combination a real code-review finding
    was about: a write tool's `approval_required` push used to bypass the masking relay
    entirely by going straight to the outer `stream_handler`, letting it reach the client
    out of the real production order relative to answer chunks still working their way
    through `_relay_masked_output_stream`'s extra async hop."""

    reply = "fake reply"

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
                                delta=StreamChunkChoiceDelta(content=chunk_text, source="test-agent", step="answer")
                            )
                        ]
                    )
                    callback.on_node_execute_stream({}, chunk=stream_chunk.model_dump(), run_id=config.run_id)
                callback.on_node_execute_stream(
                    {},
                    chunk={
                        "dynamiq_console_event": "approval_required",
                        "approval_id": "approval-1",
                        "tool": "create-order",
                    },
                    run_id=config.run_id,
                )
        return SimpleNamespace(output={"content": type(self).reply}, status=RunnableStatus.SUCCESS)


@pytest.fixture
def fake_streaming_agent_pushing_approval_required(monkeypatch):
    import dynamiq.nodes.agents

    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _FakeStreamingAgentPushingApprovalRequired)
    monkeypatch.setattr(compiler, "build_llm", lambda *_args, **_kwargs: (None, None))


@requires_postgres
async def test_stream_preserves_event_order_with_output_guardrail_and_approval_together(
    fake_streaming_agent_pushing_approval_required, gateway_async_client, company_ids, make_company,
    resolvable_agent_version, make_deployment, make_api_key
) -> None:
    company_id = company_ids()
    make_company(company_id)
    # on_error must be explicit here: left unset, it defaults to "block" for a gateway
    # run (PRD §6.4's on_detector_error split, source="api") - the gateway equivalent of
    # test_playground_run.py's chunk-safe streaming test, which gets away with leaving
    # it unset only because Playground's default is "flag" instead.
    guardrails = {
        "output": [{"check_type": "regex_blocklist", "action": "mask", "on_error": "flag", "pattern": r"reply"}]
    }
    version_id = await resolvable_agent_version(company_id, guardrails=guardrails)
    deployment_id, slug = make_deployment(company_id, version_id)
    _key_id, full_key = make_api_key(company_id, [deployment_id])

    resp = await gateway_async_client.post(
        f"/v1/deployments/{slug}/stream", json={"input": "hi"}, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 200
    events = _parse_sse_events(resp.text)
    token_events = [e for e in events if e[0] == "token"]
    approval_events = [e for e in events if e[0] == "approval_required"]

    assert "".join(event[1]["delta"] for event in token_events) == "fake [REDACTED]"
    assert len(approval_events) == 1
    # The fake agent above pushes the approval marker *after* both answer chunks, in
    # real production order - it must still land after them here, not race ahead because
    # it bypassed per-chunk masking (see this test's class docstring).
    assert events.index(approval_events[0]) > max(events.index(event) for event in token_events)
