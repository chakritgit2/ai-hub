"""create runtime.conversations table

Revision ID: 0001
Revises:
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS runtime")

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_user_id", sa.String(length=255), nullable=False,
                   comment="Hashed, per PRD §6.2/§7.5 - never the raw end-user identifier."),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="runtime",
    )
    op.create_index(
        "ix_runtime_conversations_company_id",
        "conversations",
        ["company_id"],
        schema="runtime",
    )
    op.create_index(
        "ix_runtime_conversations_deployment_id",
        "conversations",
        ["deployment_id"],
        schema="runtime",
    )
    op.execute("ALTER TABLE runtime.conversations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runtime.conversations FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("conversations", schema="runtime")
