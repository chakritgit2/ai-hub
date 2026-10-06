"""create runtime.kb_documents table

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kb_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False,
                   comment="console.knowledge_bases.id - cross-schema, no FK (console is Phinx-owned)."),
        sa.Column("okf_id", sa.String(length=255), nullable=False,
                   comment="OKF frontmatter `id`, or the file path when frontmatter omits it (PRD §6.6)."),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True, comment="Zip folder path, if imported from one."),
        sa.Column("frontmatter", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("object_key", sa.String(length=1024), nullable=False, comment="MinIO object key for the raw .md."),
        sa.Column("content_hash", sa.String(length=64), nullable=False, comment="sha256 hex of the raw file content."),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued",
                  comment="queued | processing | ready | failed"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('queued', 'processing', 'ready', 'failed')",
                            name="kb_documents_status_check"),
        sa.UniqueConstraint("kb_id", "okf_id", name="kb_documents_kb_id_okf_id_unique"),
        schema="runtime",
    )
    op.create_index("ix_runtime_kb_documents_company_id", "kb_documents", ["company_id"], schema="runtime")
    op.create_index("ix_runtime_kb_documents_kb_id", "kb_documents", ["kb_id"], schema="runtime")

    op.execute("ALTER TABLE runtime.kb_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runtime.kb_documents FORCE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.kb_documents TO ai_app")
    op.execute(
        """
        CREATE POLICY company_isolation ON runtime.kb_documents
          USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON runtime.kb_documents")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.kb_documents FROM ai_app")
    op.drop_table("kb_documents", schema="runtime")
