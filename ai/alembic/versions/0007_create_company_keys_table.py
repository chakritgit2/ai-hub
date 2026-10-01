"""create runtime.company_keys table

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-01

Backs PRD §7.7/§8.2 - one row per company holding that company's data key
(DEK), itself wrapped (encrypted) with CONSOLE_MASTER_KEY so a DB dump alone
never exposes it (see app/core/crypto.py). Primary key is company_id
directly (no surrogate id) - §7.7 is explicit that each company has exactly
one DEK, so "get or create" (app/services/company_keys.py) is a
straightforward upsert-by-PK.

destroyed_at is nullable and unused by this slice (full company-deletion /
crypto-shredding flow is out of scope here, PRD §12) - the column exists now
so that flow doesn't need its own migration later.

Applies the RLS policy + ai_app grant in the same migration that creates the
table (unlike 0002_create_connection_secrets_table.py, which deferred this
and left the table completely unreadable/unwritable until
0008_connection_secrets_rls_and_grants.py fixed it - doing it here from the
start avoids repeating that gap). No DELETE grant: destroying a DEK is a
deliberate future crypto-shredding flow that should set destroyed_at under
its own audited path, not a bare SQL DELETE.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_keys",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "wrapped_dek",
            sa.LargeBinary(),
            nullable=False,
            comment="Company DEK, AES-256-GCM-wrapped with CONSOLE_MASTER_KEY - see app/core/crypto.py.",
        ),
        sa.Column("master_key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        schema="runtime",
    )

    op.execute("ALTER TABLE runtime.company_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runtime.company_keys FORCE ROW LEVEL SECURITY")
    # Same shape as runtime.conversations's company_isolation policy (migration 0004).
    op.execute(
        """
        CREATE POLICY company_isolation ON runtime.company_keys
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON runtime.company_keys TO ai_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON runtime.company_keys FROM ai_app")
    op.execute("DROP POLICY IF EXISTS company_isolation ON runtime.company_keys")
    op.drop_table("company_keys", schema="runtime")
