"""create logs.runs table

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS logs")

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, comment="playground | api | eval"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("agent_name", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("input", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(), nullable=True),
        sa.Column("guardrail_cost_usd", sa.Numeric(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="logs",
    )
    op.create_index("ix_logs_runs_company_id", "runs", ["company_id"], schema="logs")
    op.create_index("ix_logs_runs_deployment_id", "runs", ["deployment_id"], schema="logs")
    op.create_index("ix_logs_runs_conversation_id", "runs", ["conversation_id"], schema="logs")
    op.create_index("ix_logs_runs_trace_id", "runs", ["trace_id"], schema="logs")
    op.create_index("ix_logs_runs_created_at", "runs", ["created_at"], schema="logs")
    op.execute("ALTER TABLE logs.runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE logs.runs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("runs", schema="logs")
