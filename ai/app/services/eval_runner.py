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

`run_eval` actually executes an eval run: runs the agent (`app.services.runtime.
execute_agent_run`) against every dataset item with bounded concurrency (PRD §6.7's
literal "concurrency 4" - there's no separate low-priority ARQ queue infra in this
codebase, so this is a per-item semaphore inside the one job, not a second queue), scores
each item's answer with the matching `dynamiq.evaluations` metric evaluator, and persists
one `logs.eval_results` row per item.

Two PRD §6.7 metrics are deliberately NOT executed yet, rejected at creation time
(`main_runtime.py`'s `POST /internal/v1/evals`) rather than silently skipped or run -
`UNSUPPORTED_EXECUTION_METRICS` below:
- `llm_evaluator` (custom rubric) - nothing in this codebase defines where its required
  `instructions`/`inputs`/`outputs` would come from; needs its own design, a separate slice.
- `python_evaluator` - runs admin-authored code through dynamiq's RestrictedPython
  sandbox, which still allows outbound `requests`/`urllib` and has no timeout/memory
  limit - the same unresolved sandboxing question `kind: python` tools already have.

Judge LLM calls reuse the *agent's own* connection (same provider account - `judge_model`
just names a different model string on it); there is no separate judge connection/
credential concept in this slice.

**Known, documented cost-accounting gap**: `summary["total_cost_usd"]` only sums the
agent's own `logs.runs.cost_usd` rows (one per item, written by `execute_agent_run`).
Judge-LLM calls go through dynamiq's evaluator classes directly (`llm.execute()`), which
never go through `execute_agent_run`'s usage-collector/`log_run` path, so judge token
spend is not reflected in the total - flagged here rather than silently under-reported,
same "deliberately approximate, documented up front" style `estimate_eval_cost`'s own
docstring already uses.
"""
import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable
from uuid import uuid4

import tiktoken
from dynamiq.evaluations.metrics.answer_correctness import AnswerCorrectnessEvaluator
from dynamiq.evaluations.metrics.bleu_score import BleuScoreEvaluator
from dynamiq.evaluations.metrics.context_precision import ContextPrecisionEvaluator
from dynamiq.evaluations.metrics.context_recall import ContextRecallEvaluator
from dynamiq.evaluations.metrics.factual_correctness import FactualCorrectnessEvaluator
from dynamiq.evaluations.metrics.faithfulness import FaithfulnessEvaluator
from dynamiq.evaluations.metrics.rouge_score import RougeScoreEvaluator
from dynamiq.evaluations.metrics.string_metrics import (
    ExactMatchEvaluator,
    StringPresenceEvaluator,
    StringSimilarityEvaluator,
)
from dynamiq.nodes.llms import BaseLLM

from app.integrations.dynamiq_adapter import build_llm
from app.services.agent_spec import GuardrailsSpec
from app.services.agent_versions import resolve_agent_version
from app.services.connection_secrets import decrypt_connection_secret
from app.services.connections import resolve_connection
from app.services.datasets import DatasetItemRow, list_dataset_items, resolve_dataset
from app.services.egress_allowlist import list_egress_allowlist
from app.services.eval_runs import (
    EvalResultInput,
    get_eval_run,
    insert_eval_results,
    update_eval_run_status,
)
from app.services.model_pricing import ModelPricingRow, get_model_pricing
from app.services.runtime import execute_agent_run, resolve_and_compile

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


# Metrics PRD §6.7 lists but this slice doesn't execute - see module docstring. Rejected
# at creation time (main_runtime.py); the filtering in run_eval below is just a
# defensive backstop, same "never trust it was validated upstream" style
# estimate_eval_cost's own unknown-metric handling already uses.
UNSUPPORTED_EXECUTION_METRICS = frozenset({"llm_evaluator", "python_evaluator"})

# Metrics that need a dataset item's `context`/`expected_answer` to score at all -
# dynamiq's own evaluators raise a raw pydantic ValidationError on `None` with no
# graceful handling (confirmed against the installed dynamiq==0.65.0 source), so this
# codebase pre-filters per item per metric rather than relying on dynamiq to degrade.
_CONTEXT_REQUIRED_METRICS = frozenset({"faithfulness", "context_recall", "context_precision", "factual_correctness"})
_EXPECTED_ANSWER_REQUIRED_METRICS = frozenset(
    {"answer_correctness", "bleu", "rouge", "exact_match", "string_presence", "string_similarity"}
)

_EVAL_CONCURRENCY = 4


@dataclass(frozen=True)
class _MetricScore:
    score: float
    reasoning: str | None


# Every scorer below has the same (llm, questions, answers, expected_answers, contexts)
# signature so the dispatch loop in run_eval can call any of them uniformly, even though
# most ignore several of those arguments (e.g. the five non-LLM scorers ignore `llm` and
# `questions` entirely). All are synchronous/blocking (`asyncio.to_thread` wraps the
# call) - every dynamiq evaluator's run()/run_single() does blocking LLM calls in a plain
# Python loop, confirmed against the installed dynamiq==0.65.0 source.


def _score_faithfulness(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    results = FaithfulnessEvaluator(llm=llm).run(questions=questions, answers=answers, contexts=contexts).results
    return [_MetricScore(score=r.score, reasoning=r.reasoning) for r in results]


def _score_context_recall(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    results = ContextRecallEvaluator(llm=llm).run(questions=questions, contexts=contexts, answers=answers).results
    return [_MetricScore(score=r.score, reasoning=r.reasoning) for r in results]


def _score_context_precision(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    # ContextPrecisionEvaluator scores a *ranked list* of context chunks per question - a
    # dataset item only has one `context` string, so it's wrapped as a single-chunk list;
    # precision over a ranking of one degenerates to a single relevance judgment (a real
    # but accepted limitation of this dataset shape, not a bug).
    contexts_list = [[c] for c in contexts]
    results = ContextPrecisionEvaluator(llm=llm).run(
        questions=questions, answers=answers, contexts_list=contexts_list
    ).results
    return [_MetricScore(score=r.score, reasoning=r.reasoning) for r in results]


def _score_answer_correctness(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    results = AnswerCorrectnessEvaluator(llm=llm).run(
        questions=questions, answers=answers, ground_truth_answers=expected_answers
    ).results
    return [_MetricScore(score=r.score, reasoning=r.reasoning) for r in results]


def _score_factual_correctness(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    # No `questions` parameter at all - compares the answer directly against the context.
    results = FactualCorrectnessEvaluator(llm=llm).run(answers=answers, contexts=contexts).results
    return [_MetricScore(score=r.score, reasoning=r.reasoning) for r in results]


def _score_bleu(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    scores = BleuScoreEvaluator().run(ground_truth_answers=expected_answers, answers=answers)
    return [_MetricScore(score=score, reasoning=None) for score in scores]


def _score_rouge(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    scores = RougeScoreEvaluator().run(ground_truth_answers=expected_answers, answers=answers)
    return [_MetricScore(score=score, reasoning=None) for score in scores]


def _score_exact_match(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    scores = ExactMatchEvaluator().run(ground_truth_answers=expected_answers, answers=answers)
    return [_MetricScore(score=score, reasoning=None) for score in scores]


def _score_string_presence(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    scores = StringPresenceEvaluator().run(ground_truth_answers=expected_answers, answers=answers)
    return [_MetricScore(score=score, reasoning=None) for score in scores]


def _score_string_similarity(
    llm: BaseLLM, questions: list[str], answers: list[str], expected_answers: list[str | None], contexts: list[str | None]
) -> list[_MetricScore]:
    scores = StringSimilarityEvaluator().run(ground_truth_answers=expected_answers, answers=answers)
    return [_MetricScore(score=score, reasoning=None) for score in scores]


_METRIC_SCORERS: dict[str, Callable[..., list[_MetricScore]]] = {
    "faithfulness": _score_faithfulness,
    "context_recall": _score_context_recall,
    "context_precision": _score_context_precision,
    "answer_correctness": _score_answer_correctness,
    "factual_correctness": _score_factual_correctness,
    "bleu": _score_bleu,
    "rouge": _score_rouge,
    "exact_match": _score_exact_match,
    "string_presence": _score_string_presence,
    "string_similarity": _score_string_similarity,
}


@dataclass(frozen=True)
class _ItemRun:
    dataset_item_id: str
    question: str
    expected_answer: str | None
    context: str | None
    run_id: str
    answer: str | None
    cost_usd: float | None
    error: str | None


async def _run_item(
    semaphore: asyncio.Semaphore,
    compiled_definition: dict,
    guardrails: GuardrailsSpec,
    company_id: str,
    agent_version_id: str,
    item: DatasetItemRow,
) -> _ItemRun:
    # Generated here (not inside execute_agent_run) so this item's eval_results row can
    # always reference a run_id, even in the rare case execute_agent_run raises before it
    # gets far enough to write a logs.runs row itself (e.g. a missing connection secret) -
    # see execute_agent_run's own docstring for exactly which failures still log first.
    run_id = str(uuid4())
    async with semaphore:
        try:
            result = await execute_agent_run(
                compiled_definition,
                guardrails,
                company_id=company_id,
                agent_version_id=agent_version_id,
                source="eval",
                input_text=item.question,
                external_user_id=None,
                conversation_id=None,
                deployment_id=None,
                run_id=run_id,
            )
            return _ItemRun(
                dataset_item_id=item.id,
                question=item.question,
                expected_answer=item.expected_answer,
                context=item.context,
                run_id=run_id,
                answer=result["output"],
                cost_usd=result["cost_usd"],
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - one bad item must not abort the batch
            return _ItemRun(
                dataset_item_id=item.id,
                question=item.question,
                expected_answer=item.expected_answer,
                context=item.context,
                run_id=run_id,
                answer=None,
                cost_usd=None,
                error=str(exc),
            )


async def _build_judge_llm(company_id: str, compiled_definition: dict, judge_model: str | None) -> BaseLLM:
    """Reuses the *agent's own* connection for the judge LLM (confirmed with the user) -
    `judge_model` just names a different model string on that same connection, same
    `resolve_connection`/`decrypt_connection_secret`/`build_llm` calls `execute_agent_run`
    already makes for the agent's own LLM, same egress-allowlist-only-for-custom-api_base
    rule it already follows.

    Raises ValueError for a falsy `judge_model` - defensive backstop (the real check is
    `main_runtime.py`'s `createEvalRun`, at creation time): passing `{"model": None}`
    through to `build_llm` would silently build the judge LLM with `model=None` rather
    than `dynamiq_adapter.py`'s own `gpt-4o-mini` default, since `dict.get("model",
    default)` only falls back when the key is absent, not when its value is None."""
    if not judge_model:
        raise ValueError("judge_model is required for a metric that needs an LLM judge")
    connection_id = compiled_definition["agent"]["llm"]["connection"]["connection_id"]
    connection_row = await resolve_connection(company_id, connection_id)
    if connection_row is None:
        raise ValueError(f"connection {connection_id!r} not found for judge model")
    secret = await decrypt_connection_secret(company_id, connection_id)
    if secret is None:
        raise ValueError(f"no secret stored for connection {connection_id!r}")
    egress_allowlist = await list_egress_allowlist(company_id) if connection_row.api_base else None
    _connection, judge_llm = build_llm(
        connection_row.type,
        {
            "api_key": secret,
            "url": connection_row.api_base,
            "model": judge_model,
            "egress_allowlist": egress_allowlist,
        },
    )
    return judge_llm


def _build_summary(
    item_runs: list[_ItemRun],
    scores_by_item: dict[str, dict[str, float]],
    requested_metrics: list[str],
    skipped_unsupported: list[str],
    metric_errors: dict[str, str],
) -> dict:
    mean_scores: dict[str, float] = {}
    for metric in requested_metrics:
        values = [scores_by_item[ir.dataset_item_id][metric] for ir in item_runs if metric in scores_by_item[ir.dataset_item_id]]
        if values:
            mean_scores[metric] = sum(values) / len(values)

    # Sum only over items with a known cost, not "0 if unknown" - an eval run where every
    # item failed before producing a cost must report total_cost_usd as unknown (None),
    # not a misleadingly confident 0.0 (same "don't silently understate" reasoning
    # estimate_eval_cost's own total_cost handling already uses).
    known_costs = [ir.cost_usd for ir in item_runs if ir.cost_usd is not None]
    total_cost_usd = sum(known_costs) if known_costs else None

    return {
        "item_count": len(item_runs),
        "success_count": sum(1 for ir in item_runs if ir.error is None),
        "failure_count": sum(1 for ir in item_runs if ir.error is not None),
        "mean_scores": mean_scores,
        "total_cost_usd": total_cost_usd,
        "skipped_metrics": skipped_unsupported,
        "metric_errors": metric_errors,
    }


async def run_eval(company_id: str, eval_run_id: str) -> dict:
    """ARQ job body (`app/jobs/run_eval.py`). Never lets an eval crash the ARQ job loop -
    the whole body is wrapped in `try/except Exception`, with `status` forced to
    `"failed"` on any unhandled error, same discipline `kb_indexer.py::index_kb_document`
    already established for KB indexing jobs."""
    try:
        await update_eval_run_status(company_id, eval_run_id, status="running")

        eval_run = await get_eval_run(company_id, eval_run_id)
        if eval_run is None:
            raise LookupError(f"eval_run_id {eval_run_id!r} not found for this company")

        items = await list_dataset_items(company_id, eval_run.dataset_id)
        compiled_definition = await resolve_and_compile(company_id, eval_run.agent_version_id, eval_run.role)
        guardrails = GuardrailsSpec.model_validate(compiled_definition["guardrails"])

        requested_metrics = [m for m in eval_run.metrics if m not in UNSUPPORTED_EXECUTION_METRICS]
        skipped_unsupported = [m for m in eval_run.metrics if m in UNSUPPORTED_EXECUTION_METRICS]

        judge_llm: BaseLLM | None = None
        if any(METRIC_LLM_CALLS_PER_ITEM.get(metric, 0) > 0 for metric in requested_metrics):
            judge_llm = await _build_judge_llm(company_id, compiled_definition, eval_run.judge_model)

        semaphore = asyncio.Semaphore(_EVAL_CONCURRENCY)
        item_runs = list(
            await asyncio.gather(
                *[
                    _run_item(semaphore, compiled_definition, guardrails, company_id, eval_run.agent_version_id, item)
                    for item in items
                ]
            )
        )

        scores_by_item: dict[str, dict[str, float]] = {ir.dataset_item_id: {} for ir in item_runs}
        reasons_by_item: dict[str, dict[str, str | None]] = {ir.dataset_item_id: {} for ir in item_runs}
        metric_errors: dict[str, str] = {}

        for metric in requested_metrics:
            scorer = _METRIC_SCORERS.get(metric)
            if scorer is None:
                continue  # unknown metric name - ignored, same as estimate_eval_cost's handling
            eligible = [
                ir
                for ir in item_runs
                if ir.answer is not None
                and (metric not in _CONTEXT_REQUIRED_METRICS or ir.context is not None)
                and (metric not in _EXPECTED_ANSWER_REQUIRED_METRICS or ir.expected_answer is not None)
            ]
            if not eligible:
                continue
            try:
                results = await asyncio.to_thread(
                    scorer,
                    judge_llm,
                    [ir.question for ir in eligible],
                    [ir.answer for ir in eligible],
                    [ir.expected_answer for ir in eligible],
                    [ir.context for ir in eligible],
                )
            except Exception as exc:  # noqa: BLE001 - a metric's own scoring bug (confirmed
                # live: dynamiq's BleuScoreEvaluator crashes on a single-word answer with no
                # sentence-ending punctuation - its sentence splitter filters it to zero
                # sentences, then sacrebleu indexes into an empty list) must not fail the
                # whole eval run - same per-item isolation principle as _run_item, just
                # applied at the per-metric-batch level instead.
                metric_errors[metric] = str(exc)
                continue
            for ir, result in zip(eligible, results):
                scores_by_item[ir.dataset_item_id][metric] = result.score
                reasons_by_item[ir.dataset_item_id][metric] = result.reasoning

        result_rows = [
            EvalResultInput(
                dataset_item_id=ir.dataset_item_id,
                run_id=ir.run_id,
                scores=scores_by_item[ir.dataset_item_id] or None,
                reasons=reasons_by_item[ir.dataset_item_id] or None,
                error=ir.error,
            )
            for ir in item_runs
        ]
        await insert_eval_results(company_id, eval_run_id, result_rows)

        summary = _build_summary(item_runs, scores_by_item, requested_metrics, skipped_unsupported, metric_errors)
        await update_eval_run_status(company_id, eval_run_id, status="completed", summary=summary)
        return summary
    except Exception as exc:  # noqa: BLE001 - see docstring: never crash the ARQ job loop
        await update_eval_run_status(company_id, eval_run_id, status="failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}
