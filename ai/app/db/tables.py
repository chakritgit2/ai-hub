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

company_keys_table = sa.Table(
    "company_keys",
    _metadata,
    sa.Column("company_id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
    sa.Column("master_key_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
    schema="runtime",
)

connection_secrets_table = sa.Table(
    "connection_secrets",
    _metadata,
    sa.Column("connection_id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
    sa.Column("nonce", sa.LargeBinary(), nullable=False),
    sa.Column("dek_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    schema="runtime",
)

guardrail_events_table = sa.Table(
    "guardrail_events",
    _metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column("stage", sa.String(length=16), nullable=False),
    sa.Column("check", sa.String(length=64), nullable=False),
    sa.Column("action", sa.String(length=16), nullable=False),
    sa.Column("detail", postgresql.JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    schema="logs",
)
