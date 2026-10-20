"""create runtime.tool_approvals table

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-20

PRD §6.5/§8.1 - human approval gate for `write`-access tool calls. This migration only
creates the table and locks it down with RLS; 0021 grants `ai_app` access and adds the
`company_isolation` policy (same two-step split 0015/0016 used for `eval_runs`).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending",
                   comment="pending | approved | rejected | expired"),
        sa.Column("decided_by", sa.Text(), nullable=True,
                   comment="Console user id, or a gateway caller identity for an app-approved decision."),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'expired')",
                            name="tool_approvals_status_check"),
        schema="runtime",
    )
    op.create_index("ix_runtime_tool_approvals_company_id", "tool_approvals", ["company_id"], schema="runtime")
    op.create_index("ix_runtime_tool_approvals_run_id", "tool_approvals", ["run_id"], schema="runtime")
    op.execute("ALTER TABLE runtime.tool_approvals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runtime.tool_approvals FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("tool_approvals", schema="runtime")
