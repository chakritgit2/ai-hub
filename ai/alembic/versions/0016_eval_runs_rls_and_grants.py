"""add RLS policy + ai_app grants on runtime.eval_runs

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-14

Same scheme as 0014_tool_secrets_rls_and_grants.py: 0015 enabled+forced RLS but defined
no policy and granted nothing, so the table would be completely dead without this.
`ai_app` (ai-worker's run_eval job) needs full CRUD. The policy has no `TO` clause
(applies to every role, not just ai_app) so that a later `GRANT SELECT ... TO
console_app` (db/migrations/post/020_eval_runs_results_console_grants.sql, PRD §8.1)
is automatically scoped by it too - same company_isolation shape every other
runtime/logs table here uses.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.eval_runs TO ai_app")
    op.execute(
        """
        CREATE POLICY company_isolation ON runtime.eval_runs
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON runtime.eval_runs")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.eval_runs FROM ai_app")
