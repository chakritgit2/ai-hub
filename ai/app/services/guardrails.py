"""Guardrails service stub (PRD §6.4).

Real implementation runs input/output checks using Dynamiq's detectors and
validators:

    from dynamiq.nodes.detectors.prompt_injection_detector import PromptInjectionDetector
    from dynamiq.nodes.detectors.pii_detector import PIIDetector
    from dynamiq.nodes.validators.regex_match import RegexMatch
    from dynamiq.nodes.validators.valid_json import ValidJSON

Input checks: PromptInjectionDetector, PIIDetector (mask/block),
LlamaGuardDetector, max length, regex blocklist.
Output checks: LlamaGuardDetector, PIIDetector, RegexMatch blocklist,
ValidJSON/ValidChoices.
Action per check: block / mask / flag. Every triggered check is expected to
be written to `logs.guardrail_events` and shown in the trace.
"""


async def run_input_checks(text: str) -> dict:
    raise NotImplementedError("run_input_checks: guardrail input checks not yet implemented")


async def run_output_checks(text: str) -> dict:
    raise NotImplementedError("run_output_checks: guardrail output checks not yet implemented")
