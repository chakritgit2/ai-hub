"""create runtime.agent_memory table (Dynamiq PostgreSQL memory backend)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-25

Backs `dynamiq.memory.backends.postgresql.PostgreSQL`, wired in
`app/services/conversations.py`. Pre-created here (rather than letting the
backend's own `create_if_not_exist=True` path create it on first use) for two
reasons:

1. `psycopg.sql.Identifier(self.table_name)` quotes the *entire* string as one
   identifier - passing `table_name="runtime.agent_memory"` literally creates
   a table named `runtime.agent_memory` (dot and all) in whichever schema is
   first on the connecting role's `search_path` (normally `public`), NOT a
   table named `agent_memory` inside the `runtime` schema. The backend is
   configured with the unqualified `table_name="agent_memory"` and
   `create_if_not_exist=False`; `app/services/conversations.py`'s connection
   sets `search_path` to `runtime, public` right after connecting so
   unqualified DDL/DML from the backend lands in `runtime` (see that module's
   `_MemoryDBConnection`).
2. `ai_app` needs explicit grants on this table (see migration 0004's
   docstring for why nothing is reachable by default), and Alembic is the
   place those already live for this service's other tables.

Columns/index-naming match `PostgreSQL._create_table_and_indices()` exactly
(`ai/.venv/Lib/site-packages/dynamiq/memory/backends/postgresql.py`) so that
if `create_if_not_exist` is ever flipped back to `True`, Dynamiq's own
`CREATE TABLE/INDEX IF NOT EXISTS` calls are no-ops against what's here.

No RLS: Dynamiq's own schema has no `company_id` column, by design (PRD
§6.2/§7.3) - isolation for this table rests entirely on the app always
deriving `user_id`/`session_id` from a hashed, company-namespaced composite
key (`app/core/hashing.py`) and never taking either from request input
directly. There is no DB-level backstop here, unlike every other table in
this schema; that's an accepted, documented gap tracked in the PRD's own
open-questions list, not an oversight.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_memory",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("timestamp", sa.Double(), nullable=False),
        schema="runtime",
    )
    op.create_index("idx_agentmemor_timestamp", "agent_memory", ["timestamp"], schema="runtime")
    op.create_index(
        "idx_agentmemor_metadata_gin", "agent_memory", ["metadata"], schema="runtime", postgresql_using="gin"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON runtime.agent_memory TO ai_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON runtime.agent_memory FROM ai_app")
    op.drop_table("agent_memory", schema="runtime")
