"""add RLS policy + ai_app grants on runtime.connection_secrets

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-01

Migration 0002 enabled+forced RLS on runtime.connection_secrets but - the
same gap 0004_conversations_rls_and_grants.py fixed for runtime.conversations -
never defined a policy or granted anything to ai_app. With RLS forced and
zero policies, every row is invisible/unwritable to every role by default
(fail-closed), and ai_app (the role the app actually connects as, per
DATABASE_URL) had no privileges on this table at all, so it's been
completely dead since it was created. Connection secret encryption
(app/services/connection_secrets.py, PUT/POST /internal/v1/connections/{id}/*)
is the first code path that actually needs to read/write it, so it's the
first place this needed fixing.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.connection_secrets TO ai_app")
    op.execute(
        """
        CREATE POLICY company_isolation ON runtime.connection_secrets
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON runtime.connection_secrets")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.connection_secrets FROM ai_app")
