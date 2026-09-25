"""ARQ cron job: delete conversations past `expires_at` (PRD §6.2:
conversation_ttl_days, default 30), and the Dynamiq memory rows that go with
them.

Dynamiq's own PostgreSQL memory backend has no TTL/expiry concept and no
working scoped `delete()` (see `app/services/conversations.py`'s module
docstring) - this job is the only thing that ever prunes `runtime.agent_memory`.
"""
from typing import Any

import sqlalchemy as sa

from app.core.db import get_sessionmaker


async def cleanup_expired_conversations(ctx: dict[str, Any]) -> int:
    """Return the number of conversations expired.

    Runs across every company in one pass, through the SECURITY DEFINER
    function `runtime.sweep_expired_conversations()` (migration 0006) rather
    than RLS policies for `ai_app`, so request-path sessions sharing the role
    never see other companies' rows.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(sa.text("SELECT runtime.sweep_expired_conversations()"))
        return result.scalar_one()
