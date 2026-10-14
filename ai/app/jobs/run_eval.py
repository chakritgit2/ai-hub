"""ARQ job: run an evaluation run (PRD §6.7, phase 3)."""
from typing import Any

from app.services.eval_runner import run_eval as run_eval_service


async def run_eval(ctx: dict[str, Any], company_id: str, eval_run_id: str) -> dict:
    return await run_eval_service(company_id, eval_run_id)
