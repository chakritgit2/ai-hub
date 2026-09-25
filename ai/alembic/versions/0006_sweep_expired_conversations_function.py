"""add runtime.sweep_expired_conversations() for the cross-company expiry sweep

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-25

The ARQ job `app/jobs/cleanup_expired_conversations.py` has to delete expired
conversations across every company in one pass. Doing that with permissive
RLS policies for `ai_app` would let every `ai_app` session - including the
request path, which shares the role with the worker - see other companies'
expired rows. Instead the delete runs inside this SECURITY DEFINER function
(same pattern as `console.resolve_api_key`), and only the function owner gets
a policy for expired rows.

`runtime.conversations` is FORCE ROW LEVEL SECURITY, which applies to the table
owner too, so SECURITY DEFINER alone isn't enough when migrations run as
`db_owner` rather than a superuser: the owner-scoped policies below
(`TO CURRENT_USER`, i.e. the migration role that also owns the function) are
what let the function see and delete the expired rows.

The function also deletes the matching `runtime.agent_memory` rows: Dynamiq's
memory backend has no TTL, so this is the only thing that ever prunes it.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE POLICY expiry_sweep_owner_select ON runtime.conversations
          FOR SELECT TO CURRENT_USER USING (expires_at < now())
        """
    )
    op.execute(
        """
        CREATE POLICY expiry_sweep_owner_delete ON runtime.conversations
          FOR DELETE TO CURRENT_USER USING (expires_at < now())
        """
    )

    # Data-modifying CTEs always run to completion even when the outer query doesn't read
    # them, so `deleted_memory` executes although only `expired` is counted.
    op.execute(
        """
        CREATE FUNCTION runtime.sweep_expired_conversations()
        RETURNS integer
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = runtime, pg_temp
        AS $$
          WITH expired AS (
            DELETE FROM runtime.conversations WHERE expires_at < now() RETURNING id
          ),
          deleted_memory AS (
            DELETE FROM runtime.agent_memory m
            USING expired e
            WHERE m.metadata ->> 'session_id' = e.id::text
          )
          SELECT count(*)::integer FROM expired;
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION runtime.sweep_expired_conversations() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION runtime.sweep_expired_conversations() TO ai_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS runtime.sweep_expired_conversations()")
    op.execute("DROP POLICY IF EXISTS expiry_sweep_owner_delete ON runtime.conversations")
    op.execute("DROP POLICY IF EXISTS expiry_sweep_owner_select ON runtime.conversations")
