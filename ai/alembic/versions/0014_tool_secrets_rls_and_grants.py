"""add RLS policy + ai_app/gateway_app grants on runtime.tool_secrets

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-12

Same scheme as 0008_connection_secrets_rls_and_grants.py: 0013 enabled+forced RLS but
defined no policy and granted nothing, so the table would be completely dead without
this. `ai_app` (console-api's internal connections management, `PUT /internal/v1/
tools/{id}/secret`) needs full CRUD; `gateway_app` only ever *reads* a tool's secret
during a deployment run (`app.services.runtime._execute_agent_run`, connecting as
gateway_app) and never writes one (rotation stays an ai-runtime/console-api job) -
mirrors 0012_gateway_app_connection_secret_grants.py's reasoning exactly, done up front
here instead of waiting to hit the same "permission denied" surprise in production.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.tool_secrets TO ai_app")
    op.execute("GRANT SELECT ON runtime.tool_secrets TO gateway_app")
    op.execute(
        """
        CREATE POLICY company_isolation ON runtime.tool_secrets
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON runtime.tool_secrets")
    op.execute("REVOKE SELECT ON runtime.tool_secrets FROM gateway_app")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.tool_secrets FROM ai_app")
