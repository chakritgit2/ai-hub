"""create logs.eval_results table

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eval_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True,
                   comment="The logs.runs row this item's agent execution produced - "
                           "null only if the item failed before a run_id could be written."),
        sa.Column("scores", postgresql.JSONB(), nullable=True, comment="{metric_name: float}"),
        sa.Column("reasons", postgresql.JSONB(), nullable=True, comment="{metric_name: string|null}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="logs",
    )
    op.create_index("ix_logs_eval_results_company_id", "eval_results", ["company_id"], schema="logs")
    op.create_index("ix_logs_eval_results_eval_run_id", "eval_results", ["eval_run_id"], schema="logs")
    op.execute("ALTER TABLE logs.eval_results ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE logs.eval_results FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("eval_results", schema="logs")
