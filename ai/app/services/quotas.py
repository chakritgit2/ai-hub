"""Rate limiting + quota reservation for the gateway (PRD §6.3).

Counts `rate_limit_per_min`, `daily_token_limit`, `daily_cost_limit_usd` in Redis,
reset at midnight Asia/Bangkok. Before a run the gateway reserves quota for
`max_tokens` x the model's per-token output price (litellm's own `cost_per_token`,
the same pricing source `app.services.runtime._UsageCollector` uses for real usage) and
reconciles to actual usage on completion - preventing concurrent requests from
overshooting a shared daily budget between the optimistic reservation and the real
result. If Redis is down: rate limiting fails open (log and allow), but quotas fail
closed for deployments with a `daily_cost_limit_usd` set (PRD §6.3/§12 verbatim).
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis.exceptions
from litellm import cost_per_token

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

_BANGKOK = ZoneInfo("Asia/Bangkok")
_DEFAULT_MAX_TOKENS_ESTIMATE = 1000
_KEY_PREFIX = "gw"


def _bangkok_today() -> str:
    return datetime.now(_BANGKOK).strftime("%Y-%m-%d")


def _seconds_until_bangkok_midnight() -> int:
    now = datetime.now(_BANGKOK)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def _microusd(usd: float) -> int:
    return round(usd * 1_000_000)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int | None = None


@dataclass(frozen=True)
class QuotaReservation:
    allowed: bool
    reserved_tokens: int = 0
    reserved_cost_usd: float = 0.0
    reset_at: datetime | None = None
    reason: str | None = None  # set when allowed=False: "daily_token_limit" | "daily_cost_limit" | "redis_unavailable"
    # Bangkok day (and the seconds-until-midnight TTL) this reservation's counters were
    # recorded under. reconcile_quota must reuse these rather than recomputing "today"
    # itself - a request that straddles Bangkok midnight would otherwise true up a
    # different day's key than the one it actually reserved against, creating it with no
    # TTL at all (never expires) and skewing that day's real counters from a non-zero start.
    today: str | None = None
    ttl: int | None = None


class QuotaExceededError(Exception):
    """Raised by `app.services.runtime.run_deployment_agent` when the rate limit or a
    daily quota rejects the request before anything runs - callers (app.main_gateway)
    map this to 429 with `reason`/`reset_at` (PRD §6.3: "exceeding returns
    429 quota_exceeded")."""

    def __init__(self, reason: str, reset_at: datetime | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.reset_at = reset_at


async def check_rate_limit(deployment_id: str, rate_limit_per_min: int) -> RateLimitResult:
    """Fixed-window counter keyed by the current minute - a request in-flight when the
    window rolls over just starts a fresh window, which is an acceptable approximation
    for a per-minute cap (PRD doesn't specify sliding vs fixed window)."""
    minute_bucket = int(time.time() // 60)
    key = f"{_KEY_PREFIX}:ratelimit:{deployment_id}:{minute_bucket}"

    try:
        redis_client = get_redis()
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 70)  # a little past the minute, never renewed
    except redis.exceptions.RedisError:
        logger.warning("rate limit check failed open: Redis unavailable", exc_info=True)
        return RateLimitResult(allowed=True)

    return RateLimitResult(allowed=count <= rate_limit_per_min, limit=rate_limit_per_min)


async def reserve_quota(
    deployment_id: str,
    model: str,
    max_tokens: int | None,
    daily_token_limit: int | None,
    daily_cost_limit_usd: float | None,
) -> QuotaReservation:
    """Reserves `max_tokens` (or a conservative default estimate) worth of output-token
    cost against the deployment's daily budget before a run starts. A deployment with
    neither limit set always succeeds without touching Redis at all. The returned
    `reserved_tokens`/`reserved_cost_usd` must be passed to `reconcile_quota` afterward
    so it can true up by a delta rather than overwrite the shared daily counter."""
    if daily_token_limit is None and daily_cost_limit_usd is None:
        return QuotaReservation(allowed=True)

    estimated_tokens = max_tokens or _DEFAULT_MAX_TOKENS_ESTIMATE
    _prompt_cost, completion_cost_per_token = cost_per_token(model, prompt_tokens=0, completion_tokens=1)
    reserved_cost_usd = estimated_tokens * completion_cost_per_token

    today = _bangkok_today()
    ttl = _seconds_until_bangkok_midnight()
    tokens_key = f"{_KEY_PREFIX}:quota:tokens:{deployment_id}:{today}"
    cost_key = f"{_KEY_PREFIX}:quota:cost_microusd:{deployment_id}:{today}"
    reset_at = datetime.now(_BANGKOK) + timedelta(seconds=ttl)

    try:
        redis_client = get_redis()
        # One MULTI/EXEC round trip, not four independent calls - a RedisError between
        # them used to leave the tokens_key already incremented but reported back as
        # reserved_tokens=0 (the dataclass default), so reconcile_quota later no-op'd and
        # that partial increment was never corrected, permanently inflating the day's
        # counter. Atomically, either all four apply or none do.
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.incrby(tokens_key, estimated_tokens)
            pipe.expire(tokens_key, ttl)
            pipe.incrby(cost_key, _microusd(reserved_cost_usd))
            pipe.expire(cost_key, ttl)
            new_tokens, _, new_cost_microusd, _ = await pipe.execute()
    except redis.exceptions.RedisError:
        if daily_cost_limit_usd is not None:
            logger.warning("quota reservation failed closed: Redis unavailable", exc_info=True)
            return QuotaReservation(allowed=False, reason="redis_unavailable", reset_at=reset_at)
        logger.warning("quota reservation (token-only) failed open: Redis unavailable", exc_info=True)
        return QuotaReservation(allowed=True)

    if daily_token_limit is not None and new_tokens > daily_token_limit:
        await redis_client.decrby(tokens_key, estimated_tokens)
        await redis_client.decrby(cost_key, _microusd(reserved_cost_usd))
        return QuotaReservation(allowed=False, reason="daily_token_limit", reset_at=reset_at)

    if daily_cost_limit_usd is not None and new_cost_microusd > _microusd(daily_cost_limit_usd):
        await redis_client.decrby(tokens_key, estimated_tokens)
        await redis_client.decrby(cost_key, _microusd(reserved_cost_usd))
        return QuotaReservation(allowed=False, reason="daily_cost_limit", reset_at=reset_at)

    return QuotaReservation(
        allowed=True,
        reserved_tokens=estimated_tokens,
        reserved_cost_usd=reserved_cost_usd,
        reset_at=reset_at,
        today=today,
        ttl=ttl,
    )


async def reconcile_quota(
    deployment_id: str,
    reserved_tokens: int,
    reserved_cost_usd: float,
    actual_tokens: int,
    actual_cost_usd: float,
    today: str | None = None,
    ttl: int | None = None,
) -> None:
    """Adjusts the day's counters by (actual - reserved) - never overwrites the key
    outright, since other concurrent requests against the same deployment share the same
    daily counter. `reserved_tokens == 0` means `reserve_quota` never actually reserved
    anything (no limits configured on this deployment), so there's nothing to true up.

    `today`/`ttl` should be the same reservation's own `QuotaReservation.today`/`.ttl`, not
    recomputed here - a run that straddles Bangkok midnight must still true up the day it
    actually reserved against, not whatever day happens to be current when it finishes."""
    if reserved_tokens == 0 and reserved_cost_usd == 0:
        return

    today = today or _bangkok_today()
    tokens_key = f"{_KEY_PREFIX}:quota:tokens:{deployment_id}:{today}"
    cost_key = f"{_KEY_PREFIX}:quota:cost_microusd:{deployment_id}:{today}"
    token_delta = actual_tokens - reserved_tokens
    cost_delta_microusd = _microusd(actual_cost_usd) - _microusd(reserved_cost_usd)

    try:
        redis_client = get_redis()
        if token_delta != 0:
            await redis_client.incrby(tokens_key, token_delta)
        if cost_delta_microusd != 0:
            await redis_client.incrby(cost_key, cost_delta_microusd)
        if ttl is not None:
            # Defensive re-assertion of the TTL reserve_quota already set - reconciliation
            # must never be what leaves a quota counter key persisting forever.
            await redis_client.expire(tokens_key, ttl)
            await redis_client.expire(cost_key, ttl)
    except redis.exceptions.RedisError:
        # Reconciliation is best-effort cleanup of an optimistic reservation - losing it
        # only means today's counters run slightly high until midnight reset, never an
        # unbounded leak.
        logger.warning("quota reconciliation skipped: Redis unavailable", exc_info=True)
