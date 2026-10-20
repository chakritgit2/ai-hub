"""add RLS policy + ai_app grants on runtime.tool_approvals

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-20

Same scheme as 0016_eval_runs_rls_and_grants.py: 0020 enabled+forced RLS but defined no
policy and granted nothing, so the table would be completely dead without this. `ai_app`
(ai-runtime, deciding/reading approvals) needs full CRUD. The policy has no `TO` clause
so that a later `GRANT SELECT ... TO console_app` (db/migrations/post/
025_tool_approvals_console_grants.sql, PRD §8.1) is automatically scoped by it too.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.tool_approvals TO ai_app")
    op.execute(
        """
        CREATE POLICY company_isolation ON runtime.tool_approvals
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON runtime.tool_approvals")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.tool_approvals FROM ai_app")
