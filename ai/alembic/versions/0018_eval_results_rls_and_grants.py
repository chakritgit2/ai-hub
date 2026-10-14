"""add RLS policy + ai_app grants on logs.eval_results

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-14

Same scheme as 0016_eval_runs_rls_and_grants.py - see that migration's docstring.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON logs.eval_results TO ai_app")
    op.execute(
        """
        CREATE POLICY company_isolation ON logs.eval_results
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON logs.eval_results")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON logs.eval_results FROM ai_app")
