import uuid

import pytest
import redis.exceptions

from app.core.redis import get_redis
from app.services.quotas import _bangkok_today, check_rate_limit, reconcile_quota, reserve_quota

from .markers import requires_redis

_MODEL = "gpt-4o-mini"


@pytest.fixture
def deployment_id():
    return str(uuid.uuid4())


@requires_redis
async def test_rate_limit_allows_then_blocks_at_limit(deployment_id) -> None:
    for _ in range(3):
        result = await check_rate_limit(deployment_id, rate_limit_per_min=3)
        assert result.allowed

    blocked = await check_rate_limit(deployment_id, rate_limit_per_min=3)
    assert not blocked.allowed
    assert blocked.limit == 3


@requires_redis
async def test_rate_limit_fails_open_when_redis_unavailable(deployment_id, monkeypatch) -> None:
    class _BrokenRedis:
        async def incr(self, *_args, **_kwargs):
            raise redis.exceptions.ConnectionError("simulated outage")

    monkeypatch.setattr("app.services.quotas.get_redis", lambda: _BrokenRedis())

    result = await check_rate_limit(deployment_id, rate_limit_per_min=1)
    assert result.allowed


@requires_redis
async def test_reserve_quota_without_limits_skips_redis_and_always_allows(deployment_id) -> None:
    reservation = await reserve_quota(
        deployment_id, _MODEL, max_tokens=None, daily_token_limit=None, daily_cost_limit_usd=None
    )
    assert reservation.allowed
    assert reservation.reserved_tokens == 0


@requires_redis
async def test_reserve_quota_blocks_once_daily_token_limit_exceeded(deployment_id) -> None:
    first = await reserve_quota(deployment_id, _MODEL, max_tokens=600, daily_token_limit=1000, daily_cost_limit_usd=None)
    assert first.allowed
    assert first.reserved_tokens == 600

    second = await reserve_quota(deployment_id, _MODEL, max_tokens=600, daily_token_limit=1000, daily_cost_limit_usd=None)
    assert not second.allowed
    assert second.reason == "daily_token_limit"

    # the rejected reservation must not have left its own attempted spend behind
    redis_client = get_redis()
    tokens_key = f"gw:quota:tokens:{deployment_id}:{_bangkok_today()}"
    assert int(await redis_client.get(tokens_key)) == 600


@requires_redis
async def test_reserve_quota_blocks_once_daily_cost_limit_exceeded(deployment_id) -> None:
    first = await reserve_quota(
        deployment_id, _MODEL, max_tokens=1_000_000, daily_token_limit=None, daily_cost_limit_usd=0.01
    )
    assert not first.allowed
    assert first.reason == "daily_cost_limit"


@requires_redis
async def test_reconcile_quota_adjusts_by_delta_not_overwrite(deployment_id) -> None:
    """Two concurrent requests reserve against the same deployment; reconciling one must
    not clobber the other's still-outstanding reservation (the bug this function was
    specifically written to avoid - see app/services/quotas.py's own docstring)."""
    reservation_a = await reserve_quota(deployment_id, _MODEL, max_tokens=500, daily_token_limit=10_000, daily_cost_limit_usd=None)
    reservation_b = await reserve_quota(deployment_id, _MODEL, max_tokens=500, daily_token_limit=10_000, daily_cost_limit_usd=None)
    assert reservation_a.allowed and reservation_b.allowed

    redis_client = get_redis()
    tokens_key = f"gw:quota:tokens:{deployment_id}:{_bangkok_today()}"
    assert int(await redis_client.get(tokens_key)) == 1000

    # request A actually used fewer tokens than reserved; reconciling it must only
    # refund A's own overestimate, leaving B's full 500-token reservation untouched.
    await reconcile_quota(
        deployment_id, reservation_a.reserved_tokens, reservation_a.reserved_cost_usd, actual_tokens=200, actual_cost_usd=0.0
    )
    assert int(await redis_client.get(tokens_key)) == 700  # 1000 - (500 - 200)


@requires_redis
async def test_reconcile_quota_noop_when_nothing_was_reserved(deployment_id) -> None:
    # no-limits deployments never touch Redis in reserve_quota - reconcile must not
    # either, or it would create a counter for a deployment with no configured limits.
    await reconcile_quota(deployment_id, reserved_tokens=0, reserved_cost_usd=0.0, actual_tokens=123, actual_cost_usd=0.01)

    redis_client = get_redis()
    tokens_key = f"gw:quota:tokens:{deployment_id}:{_bangkok_today()}"
    assert await redis_client.get(tokens_key) is None
