"""grant gateway_app CRUD on logs.run_steps

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-21

Same bug class as 0012_gateway_app_connection_secret_grants.py: `run_deployment_agent`
(the gateway run path, connecting as `gateway_app`, not `ai_app`) calls `log_run_steps`
on every single run via the same `_execute_agent_run_body` both entry points share, same
as it already does for `logs.runs`/`logs.guardrail_events` - 0022 only granted `ai_app`,
which would make every gateway-path run fail with "permission denied for table
run_steps" the moment it reaches that call, found by comparing this table's grants
against its sibling tables' before the first live request hit it.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON logs.run_steps TO gateway_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON logs.run_steps FROM gateway_app")
