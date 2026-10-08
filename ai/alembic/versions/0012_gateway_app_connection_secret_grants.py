"""grant gateway_app the same runtime/logs access ai_app has for the run path

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-08

Every one of these grants was written only thinking about ai_app's job (compile,
Playground, the /internal/v1/connections/{id}/* management endpoints) and never
considered that the gateway's own run path (ai/app/services/runtime.py's
run_deployment_agent, connecting as gateway_app) walks the exact same tables on every
single gateway run: decrypts a connection's secret (connection_secrets + its company's
DEK in company_keys), creates/continues a conversation row, reads/writes agent memory,
and writes the run + any guardrail events. Found by actually exercising a real
deployment's /v1/deployments/{slug}/run end-to-end for the first time - it failed with
"permission denied" on each of these tables in turn. gateway_app never writes
connection_secrets/company_keys (rotation stays ai_app/console-api's job), so those two
are SELECT-only; the other four need the same SELECT/INSERT/UPDATE/DELETE ai_app has,
since the gateway itself creates/updates those rows.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON runtime.connection_secrets TO gateway_app")
    op.execute("GRANT SELECT ON runtime.company_keys TO gateway_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.conversations TO gateway_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.agent_memory TO gateway_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON logs.runs TO gateway_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON logs.guardrail_events TO gateway_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON logs.guardrail_events FROM gateway_app")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON logs.runs FROM gateway_app")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.agent_memory FROM gateway_app")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.conversations FROM gateway_app")
    op.execute("REVOKE SELECT ON runtime.company_keys FROM gateway_app")
    op.execute("REVOKE SELECT ON runtime.connection_secrets FROM gateway_app")
