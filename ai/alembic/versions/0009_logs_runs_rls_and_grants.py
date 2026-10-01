"""add RLS policies + ai_app grants on logs.runs

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-01

Migration 0003 turned on `ENABLE`/`FORCE ROW LEVEL SECURITY` on `logs.runs` but never
defined a policy or granted anything to `ai_app` - same gap as `runtime.conversations`
(fixed in 0004) and `runtime.connection_secrets` (fixed in 0008). With RLS forced and
zero policies, every row is invisible/unwritable to every role by default (fail-closed),
and `ai_app` had no privileges on this table at all, so it's been dead since creation.
Found while building the guardrails engine, whose `logs.guardrail_events.run_id` will
reference this table.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON logs.runs TO ai_app")

    op.execute(
        """
        CREATE POLICY company_isolation ON logs.runs
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON logs.runs")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON logs.runs FROM ai_app")
