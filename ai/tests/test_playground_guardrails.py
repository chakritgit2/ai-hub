import uuid
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.core.db import get_company_session
from app.db.tables import guardrail_events_table
from app.services.agent_spec import GuardrailCheckSpec, GuardrailsSpec
from app.services.runtime import run_playground_agent

from .markers import requires_postgres


class _RecordingAgent:
    """Like test_playground_run.py's _FakeAgent, but records the input it was called
    with and lets the test control what it "replies" - needed here to prove a blocked
    input never reaches the agent at all, and to exercise output-side checks against a
    specific reply."""

    calls: ClassVar[list[dict]] = []
    reply: ClassVar[str] = "fake reply"

    def __init__(self, **kwargs) -> None:
        self.memory = kwargs.get("memory")

    def run(self, input_data: dict) -> SimpleNamespace:
        type(self).calls.append(input_data)
        return SimpleNamespace(output={"content": type(self).reply})


@pytest.fixture
def recording_agent(monkeypatch):
    import dynamiq.nodes.agents

    from app.services import runtime

    _RecordingAgent.calls = []
    _RecordingAgent.reply = "fake reply"
    monkeypatch.setattr(dynamiq.nodes.agents, "Agent", _RecordingAgent)
    monkeypatch.setattr(runtime, "build_llm", lambda *_args, **_kwargs: (None, None))
    return _RecordingAgent


async def test_blocked_input_never_reaches_the_agent(recording_agent):
    guardrails = GuardrailsSpec(
        input=[GuardrailCheckSpec(check_type="max_length", action="block", max_length=5)]
    )

    result = await run_playground_agent("this input is way too long", guardrails=guardrails)

    assert result["output"] == "I can't help with that request."
    assert recording_agent.calls == []
    assert result["guardrail_events"][0].check == "max_length"


async def test_masked_input_is_what_the_agent_actually_sees(recording_agent):
    guardrails = GuardrailsSpec(
        input=[GuardrailCheckSpec(check_type="pii", action="mask")]
    )

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
                await session.execute(
                    guardrail_events_table.select().where(
                        guardrail_events_table.c.company_id == uuid.UUID(company_id)
                    )
                )
            ).mappings().all()

        assert len(rows) == 1
        assert rows[0]["check"] == "pii"
        assert rows[0]["stage"] == "input"
    finally:
        async with get_company_session(company_id) as session:
            await session.execute(
                guardrail_events_table.delete().where(
                    guardrail_events_table.c.company_id == uuid.UUID(company_id)
                )
            )
