"""create runtime.tool_secrets table

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_secrets",
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False,
                   comment="Envelope-encrypted with the company's DEK, see app/core/crypto.py - "
                           "same scheme as runtime.connection_secrets, PRD §7.4/7.7."),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="runtime",
    )
    op.create_index(
        "ix_runtime_tool_secrets_company_id",
        "tool_secrets",
        ["company_id"],
        schema="runtime",
    )
    op.execute("ALTER TABLE runtime.tool_secrets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runtime.tool_secrets FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("tool_secrets", schema="runtime")
