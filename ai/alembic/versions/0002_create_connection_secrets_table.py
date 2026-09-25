"""create runtime.connection_secrets table

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connection_secrets",
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False,
                   comment="Envelope-encrypted with the company's DEK, see app/core/crypto.py and PRD §7.4/7.7."),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("dek_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="runtime",
    )
    op.create_index(
        "ix_runtime_connection_secrets_company_id",
        "connection_secrets",
        ["company_id"],
        schema="runtime",
    )
    # No access for console_app per PRD §8.2 - RLS enabled but only ai_app has grants
    # (grants themselves are managed by the shared-structure `db/` migration set, not here).
    op.execute("ALTER TABLE runtime.connection_secrets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runtime.connection_secrets FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("connection_secrets", schema="runtime")
