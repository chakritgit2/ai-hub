"""`app.services.streaming_events` - first direct unit tests for this module (the
pre-existing `extract_answer_delta`/`extract_tool_step_event` were only ever exercised
indirectly through `test_playground_run.py`/`test_gateway_stream.py`)."""
from app.services.streaming_events import extract_approval_required_event, extract_guardrail_event


def test_extract_approval_required_event_returns_the_payload() -> None:
    event_data = {"dynamiq_console_event": "approval_required", "approval_id": "approval-1", "tool": "create-order"}

    assert extract_approval_required_event(event_data) == {"approval_id": "approval-1", "tool": "create-order"}


def test_extract_approval_required_event_ignores_a_real_dynamiq_chunk() -> None:
    """A real dynamiq streaming chunk is always the `{"choices": [{"delta": {...}}]}`
    wrapper `extract_answer_delta`/`extract_tool_step_event` parse - never shaped like our
    own synthetic marker, so this must return None for it rather than crash on the
    missing `approval_id`/`tool` keys."""
    dynamiq_chunk = {"choices": [{"delta": {"content": "hello", "step": "answer"}}]}

    assert extract_approval_required_event(dynamiq_chunk) is None


def test_extract_approval_required_event_ignores_non_dict_data() -> None:
    assert extract_approval_required_event("hello") is None
    assert extract_approval_required_event(None) is None


def test_extract_approval_required_event_ignores_an_unrelated_marker() -> None:
    assert extract_approval_required_event({"dynamiq_console_event": "something_else"}) is None


def test_extract_approval_required_event_degrades_to_none_on_a_malformed_marker() -> None:
    """A missing/wrong-typed approval_id or tool must return None, not raise - this
    parser runs inside the SSE generator's `async for event in stream_handler:` loop with
    no surrounding try/except, so an unhandled exception here would abort the whole
    response before `final`/`error` ever reaches the client."""
    assert extract_approval_required_event({"dynamiq_console_event": "approval_required"}) is None
    assert extract_approval_required_event({"dynamiq_console_event": "approval_required", "approval_id": "a"}) is None
    assert (
        extract_approval_required_event(
            {"dynamiq_console_event": "approval_required", "approval_id": 123, "tool": "x"}
        )
        is None
    )


def test_extract_guardrail_event_returns_the_payload() -> None:
    event_data = {
        "dynamiq_console_event": "guardrail",
        "stage": "input",
        "check": "pii",
        "action": "mask",
        "detail": {"categories": ["email"]},
    }

    assert extract_guardrail_event(event_data) == {
        "stage": "input",
        "check": "pii",
        "action": "mask",
        "detail": {"categories": ["email"]},
    }


def test_extract_guardrail_event_defaults_a_missing_detail_to_an_empty_dict() -> None:
    event_data = {"dynamiq_console_event": "guardrail", "stage": "output", "check": "max_length", "action": "block"}

    assert extract_guardrail_event(event_data) == {
        "stage": "output",
        "check": "max_length",
        "action": "block",
        "detail": {},
    }


def test_extract_guardrail_event_ignores_a_real_dynamiq_chunk() -> None:
    dynamiq_chunk = {"choices": [{"delta": {"content": "hello", "step": "answer"}}]}

    assert extract_guardrail_event(dynamiq_chunk) is None


def test_extract_guardrail_event_ignores_an_approval_marker() -> None:
    """The two synthetic marker types must stay mutually exclusive - a guardrail parser
    seeing an approval_required marker (which has no stage/check/action keys) must return
    None, not crash."""
    assert extract_guardrail_event(
        {"dynamiq_console_event": "approval_required", "approval_id": "a", "tool": "x"}
    ) is None


def test_extract_guardrail_event_degrades_to_none_on_a_malformed_marker() -> None:
    assert extract_guardrail_event({"dynamiq_console_event": "guardrail"}) is None
    assert extract_guardrail_event({"dynamiq_console_event": "guardrail", "stage": "input"}) is None
    assert (
        extract_guardrail_event({"dynamiq_console_event": "guardrail", "stage": "input", "check": 123, "action": "flag"})
        is None
    )
