"""create logs.run_steps table

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                   sa.ForeignKey("logs.runs.id"), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=32), nullable=False,
                   comment="guardrail_input | llm | tool_call | guardrail_output"),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False,
                   comment="success | error (node step) or block | mask | flag (guardrail step)"),
        sa.Column("input", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="logs",
    )
    op.create_index("ix_logs_run_steps_company_id", "run_steps", ["company_id"], schema="logs")
    op.create_index("ix_logs_run_steps_run_id", "run_steps", ["run_id"], schema="logs")

    op.execute("ALTER TABLE logs.run_steps ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE logs.run_steps FORCE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON logs.run_steps TO ai_app")
    op.execute(
        """
        CREATE POLICY company_isolation ON logs.run_steps
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON logs.run_steps")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON logs.run_steps FROM ai_app")
    op.drop_table("run_steps", schema="logs")
