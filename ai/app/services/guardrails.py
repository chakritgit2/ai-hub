"""Guardrail checks (PRD §6.4).

Ships every check PRD describes that's fully offline/local: `max_length`, a hand-rolled
`regex_blocklist`, a hand-rolled `pii` detector (Thai/international regex heuristics -
email, Thai national ID, phone numbers, account-like digit runs), and dynamiq's own
`ValidJSON`/`ValidChoices` validators - plus the 3 model-based detectors PRD also lists
(`PromptInjectionDetector` -> `prompt_injection`, `PIIDetector` -> `pii_model`,
`LlamaGuardDetector` -> `content_moderation`), backed by **Azure AI Content Safety**
(prompt_injection via Shield Prompt, content_moderation via Analyze Text) and **Azure AI
Language** (pii_model) rather than dynamiq's own HuggingFace/Lakera/Replicate-calling
classes: this company chose Azure over those three specifically for accuracy (a
purpose-built classifier beats an LLM-judge prompt) and first-class Thai support (Azure
AI Language ships a dedicated `THPopulationIdentificationCode` PII entity). Each needs a
`connection_id` (see `GuardrailCheckSpec`/`MODEL_BASED_CHECK_TYPES` in `app.services.
agent_spec`) pointing at a `console.connections` row of type `"azure_content_safety"` or
`"azure_language"` holding the Azure resource's endpoint (`api_base`) and API key
(secret, via the same `PUT /connections/{id}/secret` envelope-encryption every other
connection already uses) - no new table, no dynamiq classes involved at all for these 3.

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
from app.integrations.safe_http_client import SafeHttpClient, SafeHttpClientError
from app.services.agent_spec import GuardrailCheckSpec
from app.services.connection_secrets import decrypt_connection_secret
from app.services.connections import resolve_connection
from app.services.egress_allowlist import EgressAllowlistEntry, list_egress_allowlist

DEFAULT_FALLBACK_MESSAGE = "I can't help with that request."

# Verified against Microsoft's live REST API reference (learn.microsoft.com) at the time
# this was written - pin an exact api-version rather than "latest" so a future Azure
# default-version bump can't silently change response shapes under us.
_AZURE_CONTENT_SAFETY_API_VERSION = "2024-09-01"
_AZURE_LANGUAGE_API_VERSION = "2025-11-01"
# "FourSeverityLevels" (the API default) reports each category as 0/2/4/6 - treated as
# triggered at any non-zero severity, same "detected at all, let `action` decide the
# consequence" philosophy as every other check here (regex_blocklist/pii fire on any
# match, not above some configurable threshold).
_CONTENT_SAFETY_CATEGORIES = ["Hate", "SelfHarm", "Sexual", "Violence"]

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
# - `prompt_injection`/`pii_model`/`content_moderation` (MODEL_BASED_CHECK_TYPES): each is
#   a network call to Azure - checking per-chunk would mean one Azure request per streamed
#   token, far too slow/costly, and PRD §6.4 itself only asks for these to run "in windows
#   (~200 tokens and at completion)", not every chunk. Today's conservative buffered
#   fallback (wait for the full output, check once) covers this, same as valid_json above.
_CHUNK_SAFE_CHECK_TYPES: frozenset[str] = frozenset({"regex_blocklist", "pii"})


def output_checks_are_chunk_streamable(checks: list["GuardrailCheckSpec"], default_on_error: str) -> bool:
    """Whether every check in `checks` can run inline against one streamed chunk at a
    time without needing to un-send an already-emitted token (see runtime.py's
    `streamed_live`). Both `action == "block"` and an *effective* `on_error == "block"`
    are excluded regardless of check_type - a chunk already sent to the client can't be
    recalled, so a mid-stream block needs its own abort-the-stream handling, not built
    yet (today's conservative buffered fallback - wait for the full output, check once,
    send one chunk - still applies whenever any check could trigger one). `on_error` must
    be checked too, not just `action`: a check configured `action="mask"`/`"flag"` but an
    effective `on_error` of "block" (an independent field - GuardrailCheckSpec allows
    this exact combination) still produces `blocked=True` from `run_checks` if
    `_run_one` itself raises for some chunk, which `_relay_masked_output_stream` has no
    way to safely handle mid-stream (found by code-review before this was called done -
    an earlier version of this function only checked `action`).

    `default_on_error` must be the same value the caller will actually pass to
    `run_checks` (see its own docstring) - a check whose own `on_error` is `None`
    resolves to this context default, e.g. a gateway run's default of "block" disqualifies
    every check left unset, even though the raw field value isn't literally "block"."""
    return all(
        check.check_type in _CHUNK_SAFE_CHECK_TYPES
        and check.action != "block"
        and (check.on_error or default_on_error) != "block"
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


async def run_checks(
    text: str,
    checks: list[GuardrailCheckSpec],
    stage: str,
    default_on_error: str = "flag",
    company_id: str | None = None,
) -> GuardrailOutcome:
    """Runs `checks` against `text` in order, stage is "input" or "output" (for logging
    only - callers choose which list to pass). Stops at the first `block` (no point
    running further checks once the run is already going to be rejected); `mask` carries
    its redacted text forward into subsequent checks; `flag` passes the text through
    unchanged but still records the event.

    `default_on_error` (PRD §6.4: "block" for the gateway, "flag" for Playground) only
    applies to a check whose own `on_error` is `None` - an explicit "block"/"flag" on the
    check always wins, this is purely the fallback for a check that never specified one.
    Defaults to "flag" here so every existing direct caller/test that doesn't care about
    the gateway-vs-playground distinction keeps today's behavior unchanged; runtime.py's
    real callers pass the context-correct value explicitly.

    `company_id` is only needed to resolve a `MODEL_BASED_CHECK_TYPES` check's Azure
    connection/secret/egress-allowlist - `None` is fine for any `checks` list that
    contains none of those (every existing caller/test before this param existed), and
    `_run_one` raises a clear error for a model-based check if it's still missing, routed
    through the same on_error handling as any other check failure below rather than a
    confusing `TypeError`/`AttributeError` deeper in the call stack.
    """
    events: list[CheckEvent] = []
    working_text = text
    # Keyed by connection_id - a company with e.g. both an input and output
    # content_moderation check against the same Azure connection would otherwise re-resolve
    # (resolve_connection + decrypt_connection_secret + list_egress_allowlist, 3 DB round
    # trips) identical connection data once per check instead of once per call here.
    azure_cache: dict[str, tuple[str, str, list[EgressAllowlistEntry]]] = {}

    for check in checks:
        try:
            triggered, detail, masked_text = await _run_one(working_text, check, company_id, azure_cache)
            action = check.action if triggered else None
        except Exception as exc:  # noqa: BLE001 - one bad check (bug in _run_one, or a model-based check's Azure call failing) must route through on_error, not crash every check still in the loop
            triggered, detail, masked_text = True, {"error": str(exc)}, None
            action = check.on_error or default_on_error

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


async def _run_one(
    text: str,
    check: GuardrailCheckSpec,
    company_id: str | None,
    azure_cache: dict[str, tuple[str, str, list[EgressAllowlistEntry]]] | None = None,
) -> tuple[bool, dict, str | None]:
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

    if check.check_type in ("prompt_injection", "pii_model", "content_moderation"):
        if not company_id:
            raise ValueError(f"{check.check_type!r} check requires a company_id (see run_checks's docstring)")
        # Keyed by (connection_id, check_type), not connection_id alone - caching across
        # check_types would skip _resolve_azure_connection's type validation for whichever
        # check_type didn't trigger the first resolution.
        cache_key = f"{check.connection_id}:{check.check_type}"
        if azure_cache is not None and cache_key in azure_cache:
            api_base, secret, allowlist = azure_cache[cache_key]
        else:
            api_base, secret, allowlist = await _resolve_azure_connection(
                company_id, check.connection_id, check.check_type
            )
            if azure_cache is not None:
                azure_cache[cache_key] = (api_base, secret, allowlist)
        if check.check_type == "prompt_injection":
            return await _run_prompt_injection_check(text, api_base, secret, allowlist)
        if check.check_type == "content_moderation":
            return await _run_content_moderation_check(text, api_base, secret, allowlist)
        return await _run_pii_model_check(text, check, api_base, secret, allowlist)

    raise ValueError(f"unknown guardrail check type: {check.check_type!r}")


# Mirrors web/src/lib/components/agents/AgentSpecTabs.svelte's CONNECTION_TYPE_FOR_CHECK -
# the frontend only *offers* a matching connection in its dropdown, it doesn't stop a spec
# built outside the UI (a direct API call, an imported spec) from naming any connection_id,
# so this module needs its own copy of the same rule to actually enforce it.
_EXPECTED_CONNECTION_TYPE = {
    "prompt_injection": "azure_content_safety",
    "content_moderation": "azure_content_safety",
    "pii_model": "azure_language",
}


async def _resolve_azure_connection(
    company_id: str, connection_id: str | None, check_type: str
) -> tuple[str, str, list[EgressAllowlistEntry]]:
    """Shared resolution for the 3 Azure-backed checks: the connection's endpoint
    (`api_base`), its decrypted secret, and the company's current egress allowlist (fresh
    on every call, not cached across the run - same "a company's allowlist change takes
    effect on the very next call" reasoning as `SafeHttpApiCall._run_safe_request`).
    Raises plain `ValueError`s (not `SafeHttpClientError`) for a misconfigured
    connection/missing secret - these are *configuration* problems the caller should see
    distinctly from `_call_azure`'s own network/HTTP failures, but both flow through the
    same `run_checks` try/except -> on_error handling regardless."""
    if connection_id is None:
        raise ValueError("connection_id is required")
    connection = await resolve_connection(company_id, connection_id)
    if connection is None:
        raise ValueError(f"no connection found for connection_id={connection_id!r}")
    expected_type = _EXPECTED_CONNECTION_TYPE[check_type]
    if connection.type != expected_type:
        # Without this, a misrouted connection_id would send the run's own input/output
        # text plus that connection's secret to whatever host its api_base names - not just
        # a misconfiguration, a potential data/credential leak to the wrong endpoint.
        raise ValueError(
            f"connection {connection_id!r} has type {connection.type!r}, but a {check_type!r} check"
            f" requires a {expected_type!r} connection"
        )
    if not connection.api_base:
        raise ValueError(f"connection {connection_id!r} has no api_base (Azure resource endpoint) configured")
    secret = await decrypt_connection_secret(company_id, connection_id)
    if secret is None:
        raise ValueError(f"no secret stored for connection {connection_id!r}")
    allowlist = await list_egress_allowlist(company_id)
    return connection.api_base, secret, allowlist


async def _call_azure(
    api_base: str, path: str, api_version: str, secret: str, allowlist: list[EgressAllowlistEntry], body: dict
) -> dict:
    """POSTs `body` to an Azure Cognitive Services resource through `SafeHttpClient`
    (egress-allowlist-checked, SSRF-safe - same client every HTTP Tool call already goes
    through, see `app.integrations.safe_http_tool`). Raises `ValueError` (not
    `SafeHttpClientError`) for a non-2xx Azure response, folding Azure's own error body
    in - `SafeHttpClient.request()` doesn't inspect status codes itself (see its own
    docstring), so that's this function's job, not the caller's."""
    url = f"{api_base.rstrip('/')}/{path}?api-version={api_version}"
    try:
        response = await SafeHttpClient(allowlist).request(
            "POST",
            url,
            headers={"Ocp-Apim-Subscription-Key": secret, "Content-Type": "application/json"},
            json=body,
        )
    except SafeHttpClientError as exc:
        raise ValueError(f"Azure request failed: {exc}") from exc
    if response.status_code >= 400:
        raise ValueError(f"Azure returned {response.status_code}: {response.text[:500]}")
    return response.json()


async def _run_prompt_injection_check(
    text: str, api_base: str, secret: str, allowlist: list[EgressAllowlistEntry]
) -> tuple[bool, dict, str | None]:
    """Azure AI Content Safety's Shield Prompt (PRD §6.4 PromptInjectionDetector) - no
    masking (`prompt_injection` is in `_NO_MASK_CHECK_TYPES`): a prompt-injection verdict
    has no identifiable span to redact, only a yes/no judgment on the whole input."""
    result = await _call_azure(
        api_base, "contentsafety/text:shieldPrompt", _AZURE_CONTENT_SAFETY_API_VERSION, secret, allowlist,
        {"userPrompt": text, "documents": []},
    )
    attack_detected = bool(result.get("userPromptAnalysis", {}).get("attackDetected"))
    return attack_detected, {"attackDetected": attack_detected}, None


async def _run_content_moderation_check(
    text: str, api_base: str, secret: str, allowlist: list[EgressAllowlistEntry]
) -> tuple[bool, dict, str | None]:
    """Azure AI Content Safety's Analyze Text (PRD §6.4 LlamaGuardDetector equivalent) -
    `FourSeverityLevels` reports each of Hate/SelfHarm/Sexual/Violence as 0/2/4/6;
    triggered at any non-zero severity (see `_CONTENT_SAFETY_CATEGORIES`'s comment)."""
    result = await _call_azure(
        api_base, "contentsafety/text:analyze", _AZURE_CONTENT_SAFETY_API_VERSION, secret, allowlist,
        {"text": text, "categories": _CONTENT_SAFETY_CATEGORIES, "outputType": "FourSeverityLevels"},
    )
    severities = {entry["category"]: entry["severity"] for entry in result.get("categoriesAnalysis", [])}
    triggered_categories = [category for category, severity in severities.items() if severity > 0]
    return bool(triggered_categories), {"severities": severities, "triggered": triggered_categories}, None


async def _run_pii_model_check(
    text: str, check: GuardrailCheckSpec, api_base: str, secret: str, allowlist: list[EgressAllowlistEntry]
) -> tuple[bool, dict, str | None]:
    """Azure AI Language's PII Entity Recognition (PRD §6.4 PIIDetector) - unlike the
    other 2 Azure checks, this one *does* support masking: Azure returns a ready-made
    `redactedText` using its default `characterMask` policy, so there's no need to
    reimplement per-entity-offset redaction ourselves the way the regex `pii` check does."""
    result = await _call_azure(
        api_base, "language/:analyze-text", _AZURE_LANGUAGE_API_VERSION, secret, allowlist,
        {
            "kind": "PiiEntityRecognition",
            "parameters": {"modelVersion": "latest"},
            "analysisInput": {"documents": [{"id": "1", "text": text}]},
        },
    )
    results = result.get("results", {})
    documents = results.get("documents", [])
    if not documents:
        # A missing `documents` entry means Azure couldn't analyze this text at all (its own
        # per-document error, e.g. over the character limit or an unsupported language,
        # reported under `results.errors` instead) - NOT that it analyzed it and found no
        # PII. Raising here, instead of silently returning "clean", routes this through
        # run_checks's same try/except -> on_error fail-closed handling every other failure
        # in this module gets, rather than letting unscanned text through a block-configured
        # check.
        errors = results.get("errors", [])
        raise ValueError(f"Azure Language returned no document result for the PII check: {errors}")
    document = documents[0]
    entities = document.get("entities", [])
    if not entities:
        return False, {}, None
    categories = sorted({entity["category"] for entity in entities})
    masked = document.get("redactedText") if check.action == "mask" else None
    return True, {"categories": categories}, masked


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
