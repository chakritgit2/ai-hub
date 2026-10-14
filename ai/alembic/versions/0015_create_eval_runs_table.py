"""create runtime.eval_runs table

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False,
                   comment="The caller's role at creation time (node-type allowlist for "
                           "compile_spec, PRD §6.1) - persisted since run_eval executes "
                           "later, detached from any request/session."),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("judge_model", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued",
                   comment="queued | running | completed | failed"),
        sa.Column("est_cost_usd", sa.Numeric(), nullable=True),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="runtime",
    )
    op.create_index("ix_runtime_eval_runs_company_id", "eval_runs", ["company_id"], schema="runtime")
    op.create_index("ix_runtime_eval_runs_agent_version_id", "eval_runs", ["agent_version_id"], schema="runtime")
    op.create_index("ix_runtime_eval_runs_dataset_id", "eval_runs", ["dataset_id"], schema="runtime")
    op.execute("ALTER TABLE runtime.eval_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runtime.eval_runs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("eval_runs", schema="runtime")
