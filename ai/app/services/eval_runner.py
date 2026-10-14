"""Evaluation run service (PRD §6.7, phase 3).

`estimate_eval_cost` is implemented for real: resolves the dataset + the agent version's
*current* compiled definition (same `resolve_and_compile` Playground/deployment runs use),
counts real tokens in the dataset's own question/expected_answer/context text via
`tiktoken`, and prices them against `console.model_pricing`. It is deliberately a rough
estimate, not a simulation - the real token/call counts a run produces depend on prompts,
tool calls and agent behavior this function never executes. Two numbers are fixed
constants rather than measured: `_AGENT_PROMPT_OVERHEAD_TOKENS`/`_JUDGE_PROMPT_OVERHEAD_TOKENS`
(the agent's/each judge call's own system prompt and instructions, which aren't built
without actually constructing the agent/evaluator) and `_JUDGE_OUTPUT_TOKENS_PER_CALL` (a
judge's verdict+reasoning length, which varies per call). Good enough for "should this
cost $0.02 or $20 to run" - not good enough for a billing-grade quote.

`run_eval` (actually executing an eval run against `ai-worker`'s ARQ queue) is still
unimplemented - a separate, larger piece of work (real per-item agent runs + real metric
scoring + `runtime.eval_runs`/`logs.eval_results` persistence), intentionally out of scope
for this slice.
"""
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from app.services.agent_versions import resolve_agent_version
from app.services.datasets import list_dataset_items, resolve_dataset
from app.services.model_pricing import ModelPricingRow, get_model_pricing
from app.services.runtime import resolve_and_compile

# How many LLM judge calls each dynamiq.evaluations metric makes per dataset item -
# confirmed by reading each evaluator's implementation (ai/.venv/.../dynamiq/evaluations/
# metrics/*.py): Faithfulness/AnswerCorrectness/FactualCorrectness each build two internal
# LLMEvaluators (e.g. a statement-simplifier + a verdict judge), ContextRecall/Precision
# and the generic LLMEvaluator make one call, and BLEU/ROUGE/string-match/PythonEvaluator
# are local computation with no LLM call at all. These are the canonical metric name
# strings for this feature - console-api's validation must use the same set.
METRIC_LLM_CALLS_PER_ITEM: dict[str, int] = {
    "faithfulness": 2,
    "context_recall": 1,
    "context_precision": 1,
    "answer_correctness": 2,
    "factual_correctness": 2,
    "llm_evaluator": 1,
    "bleu": 0,
    "rouge": 0,
    "exact_match": 0,
    "string_presence": 0,
    "string_similarity": 0,
    "python_evaluator": 0,
}

# Rough fixed overheads (tokens) - see module docstring for why these are constants
# rather than measured. cl100k_base is close enough across modern chat models for an
# estimate; this is not meant to match any one provider's exact tokenizer.
_AGENT_PROMPT_OVERHEAD_TOKENS = 200
_JUDGE_PROMPT_OVERHEAD_TOKENS = 300
_JUDGE_OUTPUT_TOKENS_PER_CALL = 200
_DEFAULT_ANSWER_TOKENS = 150  # used when a dataset item has no expected_answer to measure


@lru_cache
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def _estimate_cost(pricing: ModelPricingRow | None, input_tokens: int, output_tokens: int) -> float | None:
    if input_tokens == 0 and output_tokens == 0:
        return 0.0
    if pricing is None:
        return None
    return (input_tokens / 1000) * pricing.input_per_1k + (output_tokens / 1000) * pricing.output_per_1k


@dataclass(frozen=True)
class _ItemTokens:
    agent_input: int
    agent_output: int
    judge_input: int
    judge_output: int


def _tokens_for_item(question: str, expected_answer: str | None, context: str | None, judge_calls: int) -> _ItemTokens:
    question_tokens = _count_tokens(question)
    # `is not None`, not truthiness - an explicit empty string is a real (zero-length)
    # answer/context, distinct from one that was never provided at all.
    answer_tokens = _count_tokens(expected_answer) if expected_answer is not None else _DEFAULT_ANSWER_TOKENS
    context_tokens = _count_tokens(context) if context is not None else 0

    judge_input = 0
    judge_output = 0
    if judge_calls:
        per_call_input = question_tokens + answer_tokens + context_tokens + _JUDGE_PROMPT_OVERHEAD_TOKENS
        judge_input = per_call_input * judge_calls
        judge_output = _JUDGE_OUTPUT_TOKENS_PER_CALL * judge_calls

    return _ItemTokens(
        agent_input=question_tokens + _AGENT_PROMPT_OVERHEAD_TOKENS,
        agent_output=answer_tokens,
        judge_input=judge_input,
        judge_output=judge_output,
    )


async def estimate_eval_cost(
    company_id: str,
    agent_version_id: str,
    dataset_id: str,
    metrics: list[str],
    judge_model: str,
    role: str,
) -> dict:
    """Raises `LookupError` if `dataset_id` or `agent_version_id` doesn't resolve for this
    company, and `ValueError` if `agent_version_id` resolves but fails to compile -
    callers distinguish all three (404 for the first two, 422 for the last), unlike
    Playground's run path (`resolve_and_compile` alone, which conflates "not found" and
    "failed to compile" into one `ValueError`) - appropriate there since an
    already-verified runtime token's agent_version_id "not found" should never happen in
    practice, but wrong here since both ids are raw user-chosen input from the
    eval-creation form. `resolve_agent_version` is called directly first so a missing
    version raises `LookupError` before `resolve_and_compile` gets a chance to turn it
    into an indistinguishable `ValueError` - the tiny duplicated lookup (resolve_and_compile
    re-resolves it internally) is cheap and worth the correct error mapping.
    """
    dataset = await resolve_dataset(company_id, dataset_id)
    if dataset is None:
        raise LookupError(f"dataset_id {dataset_id!r} not found for this company")

    items = await list_dataset_items(company_id, dataset_id)

    if await resolve_agent_version(company_id, agent_version_id) is None:
        raise LookupError(f"agent_version_id {agent_version_id!r} not found for this company")

    compiled_definition = await resolve_and_compile(company_id, agent_version_id, role)
    agent_model = compiled_definition["agent"]["llm"]["model"]

    known_metrics: list[tuple[str, int]] = []
    warnings: list[str] = []
    for metric in metrics:
        calls = METRIC_LLM_CALLS_PER_ITEM.get(metric)
        if calls is None:
            warnings.append(f"unknown metric {metric!r} ignored")
            continue
        known_metrics.append((metric, calls))

    judge_calls_per_item = sum(calls for _, calls in known_metrics)

    agent_input_tokens = 0
    agent_output_tokens = 0
    judge_input_tokens = 0
    judge_output_tokens = 0
    for item in items:
        item_tokens = _tokens_for_item(item.question, item.expected_answer, item.context, judge_calls_per_item)
        agent_input_tokens += item_tokens.agent_input
        agent_output_tokens += item_tokens.agent_output
        judge_input_tokens += item_tokens.judge_input
        judge_output_tokens += item_tokens.judge_output

    agent_pricing = await get_model_pricing(company_id, agent_model)
    judge_pricing = await get_model_pricing(company_id, judge_model)

    if agent_pricing is None:
        warnings.append(f"no pricing found for agent model {agent_model!r}")
    if judge_calls_per_item and judge_pricing is None:
        warnings.append(f"no pricing found for judge model {judge_model!r}")

    agent_cost = _estimate_cost(agent_pricing, agent_input_tokens, agent_output_tokens)
    judge_cost = _estimate_cost(judge_pricing, judge_input_tokens, judge_output_tokens)
    # `total` is only ever a real number when BOTH sides are known - a total that silently
    # dropped an unpriced side (e.g. "or 0"-ing a None into the sum) would understate the
    # real cost without any way for a caller reading `total` alone to tell it's partial;
    # `warnings` already explains which side is missing.
    total_cost = None if (agent_cost is None or judge_cost is None) else agent_cost + judge_cost

    return {
        "item_count": len(items),
        "agent_model": agent_model,
        "judge_model": judge_model,
        "metrics": [{"metric": metric, "llm_calls_per_item": calls} for metric, calls in known_metrics],
        "estimated_tokens": {
            "agent_input": agent_input_tokens,
            "agent_output": agent_output_tokens,
            "judge_input": judge_input_tokens,
            "judge_output": judge_output_tokens,
        },
        "estimated_cost_usd": {"agent": agent_cost, "judge": judge_cost, "total": total_cost},
        "warnings": warnings,
    }


async def run_eval(eval_run_id: str) -> dict:
    raise NotImplementedError("run_eval: eval execution not yet implemented")
