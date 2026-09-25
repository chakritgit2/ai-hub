"""ARQ worker settings for ai-worker (PRD §6.6/§6.7: KB indexing, evals,
conversation cleanup).

Run with: `uv run arq app.worker.WorkerSettings`
"""
from typing import ClassVar

from arq.connections import RedisSettings
from arq.cron import cron

from app.core.config import get_settings
from app.jobs.cleanup_expired_conversations import cleanup_expired_conversations
from app.jobs.index_document import index_document
from app.jobs.run_eval import run_eval

settings = get_settings()


class WorkerSettings:
    functions: ClassVar = [index_document, run_eval]
    cron_jobs: ClassVar = [
        # Daily conversation-expiry sweep (PRD §6.2 conversation_ttl_days).
        cron(cleanup_expired_conversations, hour=3, minute=0),
    ]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
