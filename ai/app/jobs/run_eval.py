"""ARQ job: run an evaluation run (PRD §6.7, phase 3)."""
from typing import Any

from app.services.eval_runner import run_eval as run_eval_service


async def run_eval(ctx: dict[str, Any], eval_run_id: str) -> dict:
    return await run_eval_service(eval_run_id)
