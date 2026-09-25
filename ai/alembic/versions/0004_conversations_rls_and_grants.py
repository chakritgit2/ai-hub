"""add RLS policies + ai_app grants on runtime.conversations

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25

Migrations 0001-0003 turned on `ENABLE`/`FORCE ROW LEVEL SECURITY` on their
tables but never defined a policy or granted anything to `ai_app` - with RLS
forced and zero policies, every row is invisible/unwritable to every role by
default (fail-closed), and `ai_app` (the role the app actually connects as,
per `DATABASE_URL`) had no privileges on these tables at all. That made
`runtime.conversations` a table nothing could ever read or write. This
migration is the first thing in this service that actually uses that table,
so it's the first place this gap needed fixing.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.conversations TO ai_app")

    # Same shape as console.*'s company_isolation policy (db/migrations/post/002_rls_policies.sql):
    # current_setting(..., true) is NULL when the caller forgot SET LOCAL app.company_id -
    # NULL = <uuid> is never true, so that fails closed rather than showing every company's rows.
    op.execute(
        """
        CREATE POLICY company_isolation ON runtime.conversations
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )

    # The cross-company expiry sweep doesn't get a policy here - it goes through the
    # runtime.sweep_expired_conversations() function (migration 0006) so ai_app sessions on the
    # request path never gain visibility into other companies' rows, expired or not.


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON runtime.conversations")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.conversations FROM ai_app")
