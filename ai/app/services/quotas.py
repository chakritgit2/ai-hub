"""Quota/rate-limit service stub (PRD §6.3).

Real implementation counts `rate_limit_per_min`, `daily_token_limit`,
`daily_cost_limit_usd` in Redis (reset at midnight Asia/Bangkok), reserving
quota for `max_tokens` x model price before a run and reconciling to actual
usage on completion. If Redis is down: rate limiting fails open, but quotas
fail closed for deployments with a `daily_cost_limit_usd` set.
"""


async def reserve_quota(deployment_id: str, estimated_tokens: int) -> bool:
    raise NotImplementedError("reserve_quota: Redis-backed quota reservation not yet implemented")


async def reconcile_quota(deployment_id: str, actual_tokens: int, actual_cost_usd: float) -> None:
    raise NotImplementedError("reconcile_quota: Redis-backed quota reconciliation not yet implemented")
