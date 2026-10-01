"""create logs.guardrail_events table

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guardrail_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("logs.runs.id"), nullable=True,
                   comment="Nullable: logs.runs has no writer yet (PRD §6.4/§8.2's Runs/Dashboard work)."),
        sa.Column("stage", sa.String(length=16), nullable=False, comment="input | output"),
        sa.Column("check", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False, comment="block | mask | flag"),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="logs",
    )
    op.create_index("ix_logs_guardrail_events_company_id", "guardrail_events", ["company_id"], schema="logs")
    op.create_index("ix_logs_guardrail_events_run_id", "guardrail_events", ["run_id"], schema="logs")

    op.execute("ALTER TABLE logs.guardrail_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE logs.guardrail_events FORCE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON logs.guardrail_events TO ai_app")
    op.execute(
        """
        CREATE POLICY company_isolation ON logs.guardrail_events
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON logs.guardrail_events")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON logs.guardrail_events FROM ai_app")
    op.drop_table("guardrail_events", schema="logs")
