"""Async Redis client singleton (PRD §11: "Redis: 3-node Sentinel - ARQ queue, rate
limits, quotas, semaphores"). `app.worker` already connects via `arq.connections.
RedisSettings.from_dsn` for the job queue; this is the separate client
`app.services.quotas` needs for rate limiting/quota counters, sharing the same
`settings.REDIS_URL`.
"""
import asyncio
from functools import lru_cache

import redis.asyncio as redis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    settings = get_settings()
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


_arq_pool: ArqRedis | None = None
_arq_pool_lock = asyncio.Lock()


async def get_arq_pool() -> ArqRedis:
    """Process-wide ARQ connection pool for enqueueing jobs onto `app.worker.
    WorkerSettings`'s queue (e.g. the `index_document` job - PRD §6.6) - `create_pool`
    is async (unlike `get_redis()`'s plain client above), so this caches via a module
    global guarded by an `asyncio.Lock` instead of `lru_cache`."""
    global _arq_pool
    if _arq_pool is None:
        async with _arq_pool_lock:
            if _arq_pool is None:
                settings = get_settings()
                _arq_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _arq_pool
