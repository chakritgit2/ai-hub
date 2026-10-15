"""add nullable workflow_version_id to logs.runs

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-15

PRD-001 §6.9 (phase 4, slice 2 - real workflow execution). `agent_version_id` is already
nullable on this table; a workflow run sets `workflow_version_id` and leaves
`agent_version_id` null, an agent run keeps doing the opposite - same convention
`deployment_id`'s existing nullability already established (not every run has every id).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("workflow_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="logs",
    )


def downgrade() -> None:
    op.drop_column("runs", "workflow_version_id", schema="logs")
