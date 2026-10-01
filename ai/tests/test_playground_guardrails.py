import uuid
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.core.db import get_company_session
from app.db.tables import guardrail_events_table, runs_table
from app.services.agent_spec import GuardrailCheckSpec, GuardrailsSpec
from app.services.runtime import _UsageCollector, run_playground_agent

from .markers import requires_postgres

_FAKE_USAGE = {
    "prompt_tokens": 12,
    "completion_tokens": 7,
    "total_tokens": 19,
    "total_tokens_cost_usd": 0.0042,
}


class _RecordingAgent:
    """Like test_playground_run.py's _FakeAgent, but records the input it was called
    with and lets the test control what it "replies" (and whether it raises) - needed
    here to prove a blocked input never reaches the agent at all, to exercise
    output-side checks against a specific reply, and to exercise the error path.
    Also fires a fake usage_data callback, like a real LLM node would, to prove the
    run_playground_agent -> _UsageCollector -> logs.runs wiring end-to-end."""

    calls: ClassVar[list[dict]] = []
    reply: ClassVar[str] = "fake reply"
    raises: ClassVar[Exception | None] = None

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict, config=None, **_kwargs) -> SimpleNamespace:
        type(self).calls.append(input_data)
        if config is not None:
            for callback in config.callbacks:
                callback.on_node_execute_run({}, usage_data=_FAKE_USAGE)
        if type(self).raises is not None:
            raise type(self).raises
        return SimpleNamespace(output={"content": type(self).reply})


@pytest.fixture
def recording_agent(monkeypatch):
    import dynamiq.nodes.agents

    from app.services import runtime

    _RecordingAgent.calls = []
    _RecordingAgent.reply = "fake reply"
    _RecordingAgent.raises = None
    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _RecordingAgent)
    monkeypatch.setattr(runtime, "build_llm", lambda *_args, **_kwargs: (None, None))
    return _RecordingAgent


async def _fetch_run_by_trace(company_id: str, trace_id: str) -> dict | None:
    # trace_id, not the internal run_id (logs.runs' PK), is the only run identifier
    # run_playground_agent's return value actually exposes to callers.
    async with get_company_session(company_id) as session:
        row = (
            (await session.execute(runs_table.select().where(runs_table.c.trace_id == trace_id)))
            .mappings()
            .one_or_none()
        )
    return dict(row) if row is not None else None


async def test_blocked_input_never_reaches_the_agent(recording_agent):
    guardrails = GuardrailsSpec(input=[GuardrailCheckSpec(check_type="max_length", action="block", max_length=5)])

    result = await run_playground_agent("this input is way too long", guardrails=guardrails)

    assert result["output"] == "I can't help with that request."
    assert recording_agent.calls == []
    assert result["guardrail_events"][0].check == "max_length"


async def test_masked_input_is_what_the_agent_actually_sees(recording_agent):
    guardrails = GuardrailsSpec(input=[GuardrailCheckSpec(check_type="pii", action="mask")])

    await run_playground_agent("my email is test@example.com", guardrails=guardrails)

    assert len(recording_agent.calls) == 1
    assert "test@example.com" not in recording_agent.calls[0]["input"]
    assert "EMAIL_REDACTED" in recording_agent.calls[0]["input"]


async def test_blocked_output_replaces_the_reply(recording_agent):
    recording_agent.reply = "here is a secret word"
    guardrails = GuardrailsSpec(
        output=[GuardrailCheckSpec(check_type="regex_blocklist", action="block", pattern=r"\bsecret\b")]
    )

    result = await run_playground_agent("hi", guardrails=guardrails)

    assert result["output"] == "I can't help with that request."


async def test_masked_output_is_returned_instead_of_the_raw_reply(recording_agent):
    recording_agent.reply = "my email is test@example.com"
    guardrails = GuardrailsSpec(output=[GuardrailCheckSpec(check_type="pii", action="mask")])

    result = await run_playground_agent("hi", guardrails=guardrails)

    assert "test@example.com" not in result["output"]
    assert "EMAIL_REDACTED" in result["output"]


async def test_no_guardrails_configured_behaves_exactly_as_before(recording_agent):
    result = await run_playground_agent("hi")

    assert result["output"] == "fake reply"
    assert result["guardrail_events"] == []


def test_usage_collector_sums_across_multiple_llm_calls():
    collector = _UsageCollector()
    collector.on_node_execute_run(
        {}, usage_data={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens_cost_usd": 0.01}
    )
    collector.on_node_execute_run(
        {}, usage_data={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens_cost_usd": 0.002}
    )
    collector.on_node_execute_run({}, usage_data=None)  # a node with no usage_data at all must not crash the sum

    usage = collector.total_usage()

    assert usage.tokens_in == 13
    assert usage.tokens_out == 7
    assert usage.cost_usd == pytest.approx(0.012)


def test_usage_collector_with_no_calls_reports_none():
    usage = _UsageCollector().total_usage()

    assert usage.tokens_in is None
    assert usage.tokens_out is None
    assert usage.cost_usd is None


@requires_postgres
async def test_triggered_checks_are_persisted_to_guardrail_events_for_a_real_company(recording_agent):
    """The full run_playground_agent -> logs.guardrail_events write path, with a real
    company_id (unlike the other tests above, which all use the default "" and so never
    reach the DB at all) - proves the wiring actually lands rows, not just that
    log_guardrail_events works in isolation (already covered by test_guardrails.py)."""
    company_id = str(uuid.uuid4())
    guardrails = GuardrailsSpec(input=[GuardrailCheckSpec(check_type="pii", action="flag")])

    try:
        result = await run_playground_agent(
            "my email is test@example.com", company_id=company_id, guardrails=guardrails
        )
        assert result["guardrail_events"]

        async with get_company_session(company_id) as session:
            rows = (
                (
                    await session.execute(
                        guardrail_events_table.select().where(
                            guardrail_events_table.c.company_id == uuid.UUID(company_id)
                        )
                    )
                )
                .mappings()
                .all()
            )

        assert len(rows) == 1
        assert rows[0]["check"] == "pii"
        assert rows[0]["stage"] == "input"
        # The FK only succeeds if logs.runs' row was written before this one - proves
        # log_run() -> log_guardrail_events() ordering, not just that each works alone.
        assert rows[0]["run_id"] is not None
    finally:
        async with get_company_session(company_id) as session:
            await session.execute(
                guardrail_events_table.delete().where(guardrail_events_table.c.company_id == uuid.UUID(company_id))
            )
            await session.execute(runs_table.delete().where(runs_table.c.company_id == uuid.UUID(company_id)))


@requires_postgres
async def test_successful_run_is_persisted_to_logs_runs_with_real_usage(recording_agent):
    company_id = str(uuid.uuid4())

    try:
        result = await run_playground_agent("hi there", company_id=company_id)
        run = await _fetch_run_by_trace(company_id, result["trace_id"])

        assert run is not None
        assert run["status"] == "success"
        assert run["source"] == "playground"
        assert run["agent_name"] == "playground-agent"
        assert run["output"] == "fake reply"
        assert run["tokens_in"] == 12
        assert run["tokens_out"] == 7
        assert float(run["cost_usd"]) == pytest.approx(0.0042)
    finally:
        async with get_company_session(company_id) as session:
            await session.execute(runs_table.delete().where(runs_table.c.company_id == uuid.UUID(company_id)))


@requires_postgres
async def test_blocked_run_is_persisted_with_blocked_status(recording_agent):
    company_id = str(uuid.uuid4())
    guardrails = GuardrailsSpec(input=[GuardrailCheckSpec(check_type="max_length", action="block", max_length=5)])

    try:
        result = await run_playground_agent("this is way too long", company_id=company_id, guardrails=guardrails)
        run = await _fetch_run_by_trace(company_id, result["trace_id"])

        assert run is not None
        assert run["status"] == "blocked"
        assert recording_agent.calls == []
    finally:
        async with get_company_session(company_id) as session:
            # guardrail_events.run_id FKs to runs - delete it first, same lesson as
            # runtime.py's own log_run()-before-log_guardrail_events() write ordering.
            await session.execute(
                guardrail_events_table.delete().where(
                    guardrail_events_table.c.company_id == uuid.UUID(company_id)
                )
            )
            await session.execute(runs_table.delete().where(runs_table.c.company_id == uuid.UUID(company_id)))


@requires_postgres
async def test_agent_error_is_persisted_with_error_status_and_still_raises(recording_agent):
    company_id = str(uuid.uuid4())
    recording_agent.raises = RuntimeError("boom from the fake agent")

    try:
        with pytest.raises(RuntimeError, match="boom from the fake agent"):
            await run_playground_agent("hi", company_id=company_id)

        async with get_company_session(company_id) as session:
            rows = (
                (await session.execute(runs_table.select().where(runs_table.c.company_id == uuid.UUID(company_id))))
                .mappings()
                .all()
            )

        assert len(rows) == 1
        assert rows[0]["status"] == "error"
        assert "boom from the fake agent" in rows[0]["error"]
    finally:
        async with get_company_session(company_id) as session:
            await session.execute(runs_table.delete().where(runs_table.c.company_id == uuid.UUID(company_id)))
