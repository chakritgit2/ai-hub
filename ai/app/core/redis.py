"""Async Redis client singleton (PRD §11: "Redis: 3-node Sentinel - ARQ queue, rate
limits, quotas, semaphores"). `app.worker` already connects via `arq.connections.
RedisSettings.from_dsn` for the job queue; this is the separate client
`app.services.quotas` needs for rate limiting/quota counters, sharing the same
`settings.REDIS_URL`.
"""
from functools import lru_cache

import redis.asyncio as redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    settings = get_settings()
    return redis.from_url(settings.REDIS_URL, decode_responses=True)
