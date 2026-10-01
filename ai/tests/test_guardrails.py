import uuid

from app.core.db import get_company_session
from app.db.tables import guardrail_events_table
from app.services.agent_spec import GuardrailCheckSpec
from app.services.guardrails import CheckEvent, log_guardrail_events, run_checks

from .markers import requires_postgres


def _check(**kwargs) -> GuardrailCheckSpec:
    defaults = {"check_type": "max_length", "action": "block", "max_length": 10}
    return GuardrailCheckSpec(**{**defaults, **kwargs})


async def test_max_length_triggers_when_text_too_long():
    outcome = await run_checks("this is way too long", [_check(max_length=5)], stage="input")

    assert outcome.blocked is True
    assert outcome.fallback_message
    assert outcome.events[0].check == "max_length"
    assert outcome.events[0].action == "block"


async def test_max_length_does_not_trigger_within_limit():
    outcome = await run_checks("short", [_check(max_length=10)], stage="input")

    assert outcome.blocked is False
    assert outcome.text == "short"
    assert outcome.events == []


async def test_regex_blocklist_blocks():
    check = _check(check_type="regex_blocklist", action="block", pattern=r"\bsecret\b", max_length=None)
    outcome = await run_checks("this contains a secret word", [check], stage="input")

    assert outcome.blocked is True
    assert outcome.events[0].detail["matched"] == "secret"


async def test_regex_blocklist_masks():
    check = _check(check_type="regex_blocklist", action="mask", pattern=r"\bsecret\b", max_length=None)
    outcome = await run_checks("this contains a secret word", [check], stage="output")

    assert outcome.blocked is False
    assert outcome.text == "this contains a [REDACTED] word"


async def test_regex_blocklist_flags_without_changing_text():
    check = _check(check_type="regex_blocklist", action="flag", pattern=r"\bsecret\b", max_length=None)
    outcome = await run_checks("this contains a secret word", [check], stage="output")

    assert outcome.blocked is False
    assert outcome.text == "this contains a secret word"
    assert outcome.events[0].action == "flag"


async def test_pii_detects_email_and_blocks():
    check = _check(check_type="pii", action="block", max_length=None)
    outcome = await run_checks("contact me at test@example.com", [check], stage="input")

    assert outcome.blocked is True
    assert "email" in outcome.events[0].detail["categories"]


async def test_pii_masks_thai_national_id():
    check = _check(check_type="pii", action="mask", max_length=None)
    outcome = await run_checks("my id is 1-2345-67890-12-3", [check], stage="input")

    assert outcome.blocked is False
    assert "1-2345-67890-12-3" not in outcome.text
    assert "THAI_NATIONAL_ID_REDACTED" in outcome.text


async def test_pii_no_trigger_on_clean_text():
    check = _check(check_type="pii", action="block", max_length=None)
    outcome = await run_checks("just a normal sentence", [check], stage="input")

    assert outcome.blocked is False
    assert outcome.events == []


async def test_valid_json_blocks_invalid_json():
    check = _check(check_type="valid_json", action="block", max_length=None)
    outcome = await run_checks("not json at all", [check], stage="output")

    assert outcome.blocked is True


async def test_valid_json_passes_real_json():
    check = _check(check_type="valid_json", action="block", max_length=None)
    outcome = await run_checks('{"ok": true}', [check], stage="output")

    assert outcome.blocked is False


async def test_valid_choices_blocks_unknown_choice():
    check = _check(check_type="valid_choices", action="block", choices=["yes", "no"], max_length=None)
    outcome = await run_checks("maybe", [check], stage="output")

    assert outcome.blocked is True


async def test_valid_choices_passes_known_choice():
    check = _check(check_type="valid_choices", action="block", choices=["yes", "no"], max_length=None)
    outcome = await run_checks("yes", [check], stage="output")

    assert outcome.blocked is False


async def test_block_stops_before_running_later_checks():
    blocking = _check(check_type="max_length", action="block", max_length=1)
    later = _check(check_type="regex_blocklist", action="block", pattern=r"never-reached", max_length=None)
    outcome = await run_checks("too long for the first check", [blocking, later], stage="input")

    assert outcome.blocked is True
    assert len(outcome.events) == 1
    assert outcome.events[0].check == "max_length"


async def test_mask_carries_forward_into_later_checks():
    # "a secret value" is 15 chars; masked to "a [REDACTED] value" is 19 - a max_length of
    # 17 only trips if the second check actually sees the masked (longer) text, not the
    # original, proving mask output really carries forward rather than being discarded.
    masking = _check(check_type="regex_blocklist", action="mask", pattern=r"\bsecret\b", max_length=None)
    reporting = _check(check_type="max_length", action="flag", max_length=17)
    outcome = await run_checks("a secret value", [masking, reporting], stage="output")

    assert outcome.blocked is False
    assert outcome.text == "a [REDACTED] value"
    assert len(outcome.events) == 2
    assert outcome.events[1].check == "max_length"


async def test_on_error_block_propagates_as_a_block(monkeypatch):
    import app.services.guardrails as guardrails_module

    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(guardrails_module, "_run_one", _raise)
    check = _check(check_type="max_length", action="block", on_error="block", max_length=5)

    outcome = await run_checks("irrelevant", [check], stage="input")

    assert outcome.blocked is True
    assert outcome.events[0].detail["error"] == "boom"


async def test_on_error_flag_lets_the_run_continue(monkeypatch):
    import app.services.guardrails as guardrails_module

    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(guardrails_module, "_run_one", _raise)
    check = _check(check_type="max_length", action="block", on_error="flag", max_length=5)

    outcome = await run_checks("irrelevant", [check], stage="input")

    assert outcome.blocked is False
    assert outcome.events[0].action == "flag"


@requires_postgres
async def test_log_guardrail_events_writes_rows_scoped_by_company():
    company_id = str(uuid.uuid4())
    events = [
        CheckEvent(stage="input", check="pii", action="block", detail={"categories": ["email"]}),
        CheckEvent(stage="output", check="valid_json", action="flag", detail={"error": "bad json"}),
    ]

    try:
        await log_guardrail_events(company_id, run_id=None, events=events)

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

        assert len(rows) == 2
        checks = {row["check"] for row in rows}
        assert checks == {"pii", "valid_json"}
    finally:
        async with get_company_session(company_id) as session:
            await session.execute(
                guardrail_events_table.delete().where(guardrail_events_table.c.company_id == uuid.UUID(company_id))
            )


async def test_log_guardrail_events_is_a_noop_for_empty_events():
    # No company_id/events means no DB round trip at all - this must not raise even
    # without a real company context, and needs no Postgres connection to verify.
    await log_guardrail_events("", run_id=None, events=[])
