"""Evaluation run service stub (PRD §6.7, phase 3).

Real implementation processes dataset items on a low-priority ARQ queue
(concurrency 4), computing metrics (Faithfulness, Context Recall/Precision,
Answer/Factual Correctness, BLEU/ROUGE/string match, LLMEvaluator,
PythonEvaluator) and persisting per-item results plus a run summary.
"""


async def estimate_eval_cost(company_id: str, dataset_id: str, metrics: list[str], judge_model: str) -> dict:
    raise NotImplementedError("estimate_eval_cost: eval cost estimation not yet implemented")


async def run_eval(eval_run_id: str) -> dict:
    raise NotImplementedError("run_eval: eval execution not yet implemented")
