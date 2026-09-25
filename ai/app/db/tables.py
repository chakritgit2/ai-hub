"""Hand-written SQLAlchemy Core table definitions for `runtime`/`logs` tables.

No ORM/declarative models exist in this service (see `app/core/db.py` and
`ai/alembic/env.py`'s `target_metadata = None`) - migrations are hand-written
and these `sa.Table` objects are kept in sync with them by hand. Defined once
here, shared by every service module that needs to query one of these tables
via SQLAlchemy Core, instead of each module re-deriving its own `sa.text()`.

Column sets must match the corresponding `ai/alembic/versions/*.py` migration
exactly - there is no autogenerate/drift check for this today.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

_metadata = sa.MetaData()

conversations_table = sa.Table(
    "conversations",
    _metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column("source", sa.String(length=32), nullable=False),
    sa.Column("external_user_id", sa.String(length=255), nullable=False),
    sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    schema="runtime",
)
