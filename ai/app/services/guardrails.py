"""Guardrail checks (PRD §6.4).

Ships every check PRD describes that's fully offline/local: `max_length`, a hand-rolled
`regex_blocklist`, a hand-rolled `pii` detector (Thai/international regex heuristics -
email, Thai national ID, phone numbers, account-like digit runs), and dynamiq's own
`ValidJSON`/`ValidChoices` validators. The three model-based detectors PRD also lists
(`PromptInjectionDetector`, `PIIDetector`, `LlamaGuardDetector`) each require a live call
to HuggingFace/Lakera/Replicate respectively - dynamiq ships the classes but no local/
bundled model, and no credentials for any of the three are configured here - so they're
deliberately left for a follow-up once that's available to verify against, rather than
wiring something that can only ever be unit-tested with everything mocked.

Not `dynamiq.nodes.validators.RegexMatch` for `regex_blocklist`, despite the name:
`RegexMatch.validate()` raises when its pattern does *not* match - it's an allowlist/
format validator ("valid only if this matches"), the opposite of a blocklist ("blocked if
this matches"). Using it here would need a double-negated pattern and invite exactly the
kind of bug this note exists to prevent; a plain `re.search`/`re.sub` says what it means.
"""
import re
import uuid
from dataclasses import dataclass, field

from dynamiq.nodes.validators.valid_choices import ValidChoices
from dynamiq.nodes.validators.valid_json import ValidJSON

from app.core.db import get_company_session
from app.db.tables import guardrail_events_table
from app.services.agent_spec import GuardrailCheckSpec

DEFAULT_FALLBACK_MESSAGE = "I can't help with that request."

# Thai/international "fast checks" (PRD §6.4) - cheap regex heuristics, not a full NER
# model. Deliberately approximate: e.g. account_number's digit-run pattern will also
# match inside a Thai national ID or phone number, but since matched text is redacted
# with non-digit placeholder text before the next pattern runs, earlier matches can't be
# re-matched by a later, broader pattern - over-reporting a category on the same already-
# redacted span is the only side effect, never a double-substitution artifact.
_PII_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "thai_national_id": re.compile(r"\b\d-\d{4}-\d{5}-\d{2}-\d\b|\b\d{13}\b"),
    "phone": re.compile(r"\b(?:\+\d{1,3}[-\s]?)?0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{3,4}\b"),
    "account_number": re.compile(r"\b\d{10,15}\b"),
}

# Check types whose result against one arrived-so-far text chunk means the same thing it
# would against the full text (PRD §6.4 "every chunk passes fast checks... before
# sending"). Excluded:
# - `valid_json`/`valid_choices`: a partial chunk is never complete JSON or a full choice
#   string, so running them chunk-by-chunk would reject every chunk up until the very
#   last one - meaningless before the output is actually complete.
# - `max_length`: it's a property of the *complete* output, not of any one small delta -
#   `len(delta) > max_length` almost never trips against a handful of streamed characters
#   even when the full assembled answer badly exceeds the limit, which would silently
#   defeat the check rather than just run it less precisely (found by code-review before
#   this was called done).
_CHUNK_SAFE_CHECK_TYPES: frozenset[str] = frozenset({"regex_blocklist", "pii"})


def output_checks_are_chunk_streamable(checks: list["GuardrailCheckSpec"]) -> bool:
    """Whether every check in `checks` can run inline against one streamed chunk at a
    time without needing to un-send an already-emitted token (see runtime.py's
    `streamed_live`). Both `action == "block"` and `on_error == "block"` are excluded
    regardless of check_type - a chunk already sent to the client can't be recalled, so a
    mid-stream block needs its own abort-the-stream handling, not built yet (today's
    conservative buffered fallback - wait for the full output, check once, send one
    chunk - still applies whenever any check could trigger one). `on_error` must be
    checked too, not just `action`: a check configured `action="mask"`/`"flag"` but
    `on_error="block"` (an independent field - GuardrailCheckSpec allows this exact
    combination) still produces `blocked=True` from `run_checks` if `_run_one` itself
    raises for some chunk, which `_relay_masked_output_stream` has no way to safely
    handle mid-stream (found by code-review before this was called done - an earlier
    version of this function only checked `action`)."""
    return all(
        check.check_type in _CHUNK_SAFE_CHECK_TYPES and check.action != "block" and check.on_error != "block"
        for check in checks
    )


@dataclass(frozen=True)
class CheckEvent:
    """One triggered check, ready to log to `logs.guardrail_events`."""

    stage: str  # "input" | "output"
    check: str  # GuardrailCheckSpec.check_type
    action: str  # the action actually taken: "block" | "mask" | "flag"
    detail: dict


@dataclass(frozen=True)
class GuardrailOutcome:
    blocked: bool
    text: str  # original text, or masked, or unchanged if blocked (caller uses fallback_message instead)
    fallback_message: str | None
    events: list[CheckEvent] = field(default_factory=list)


async def run_checks(text: str, checks: list[GuardrailCheckSpec], stage: str) -> GuardrailOutcome:
    """Runs `checks` against `text` in order, stage is "input" or "output" (for logging
    only - callers choose which list to pass). Stops at the first `block` (no point
    running further checks once the run is already going to be rejected); `mask` carries
    its redacted text forward into subsequent checks; `flag` passes the text through
    unchanged but still records the event.
    """
    events: list[CheckEvent] = []
    working_text = text

    for check in checks:
        try:
            triggered, detail, masked_text = _run_one(working_text, check)
            action = check.action if triggered else None
        except Exception as exc:  # noqa: BLE001 - one bad check (bug in _run_one) must route through on_error, not crash every check still in the loop
            triggered, detail, masked_text = True, {"error": str(exc)}, None
            action = check.on_error

        if not triggered:
            continue

        events.append(CheckEvent(stage=stage, check=check.check_type, action=action, detail=detail))

        if action == "block":
            return GuardrailOutcome(
                blocked=True,
                text=working_text,
                fallback_message=check.fallback_message or DEFAULT_FALLBACK_MESSAGE,
                events=events,
            )
        if action == "mask" and masked_text is not None:
            working_text = masked_text

    return GuardrailOutcome(blocked=False, text=working_text, fallback_message=None, events=events)


def _run_one(text: str, check: GuardrailCheckSpec) -> tuple[bool, dict, str | None]:
    """Returns (triggered, detail, masked_text). masked_text is only set when the check
    triggered with action="mask" - None otherwise (nothing to carry forward)."""
    if check.check_type == "max_length":
        length = len(text)
        return length > check.max_length, {"length": length, "max_length": check.max_length}, None

    if check.check_type == "regex_blocklist":
        match = re.search(check.pattern, text)
        if match is None:
            return False, {}, None
        masked = re.sub(check.pattern, "[REDACTED]", text) if check.action == "mask" else None
        return True, {"pattern": check.pattern, "matched": match.group(0)}, masked

    if check.check_type == "pii":
        return _run_pii_check(text, check)

    if check.check_type == "valid_json":
        try:
            ValidJSON().validate(text)
            return False, {}, None
        except ValueError as exc:
            return True, {"error": str(exc)}, None

    if check.check_type == "valid_choices":
        try:
            ValidChoices(choices=check.choices).validate(text)
            return False, {}, None
        except ValueError as exc:
            return True, {"error": str(exc), "choices": check.choices}, None

    raise ValueError(f"unknown guardrail check type: {check.check_type!r}")


def _run_pii_check(text: str, check: GuardrailCheckSpec) -> tuple[bool, dict, str | None]:
    categories_found: list[str] = []
    masked = text
    for category, pattern in _PII_PATTERNS.items():
        # Checks against `masked`, not `text`: once action="mask" has redacted an earlier
        # category's span (e.g. a phone number) to non-digit placeholder text, a later,
        # broader pattern (e.g. account_number) must not still "find" that same already-
        # redacted span in the original text and get reported as a present category when
        # the actual output no longer contains it. For action != "mask", `masked` is never
        # reassigned below, so this is equivalent to checking `text` every time, same as
        # before.
        if pattern.search(masked) is None:
            continue
        categories_found.append(category)
        if check.action == "mask":
            masked = pattern.sub(f"[{category.upper()}_REDACTED]", masked)

    if not categories_found:
        return False, {}, None
    return True, {"categories": categories_found}, masked if check.action == "mask" else None


async def log_guardrail_events(company_id: str, run_id: str | None, events: list[CheckEvent]) -> None:
    """Writes every triggered check to `logs.guardrail_events` (PRD §6.4). A no-op for an
    empty `events` list or a blank `company_id` (no company context to scope the write
    to - same convention as `app.services.conversations`)."""
    if not events or not company_id:
        return

    async with get_company_session(company_id) as session:
        await session.execute(
            guardrail_events_table.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "company_id": uuid.UUID(company_id),
                    "run_id": uuid.UUID(run_id) if run_id else None,
                    "stage": event.stage,
                    "check": event.check,
                    "action": event.action,
                    "detail": event.detail,
                }
                for event in events
            ],
        )
