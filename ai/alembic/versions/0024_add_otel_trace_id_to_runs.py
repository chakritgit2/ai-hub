"""add nullable otel_trace_id to logs.runs

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-05

The REAL OpenTelemetry/Tempo trace id (32 lowercase hex chars), captured from the active
span OpenLIT creates during agent.run() - see app.services.runtime's _StepCollector,
which stores it from trace.get_current_span().get_span_context().trace_id the first time
a node starts, since no span is active before/after agent.run() itself.

NOT the same thing as this table's existing `trace_id` column: that one is an unrelated
uuid4() this service generates itself (app.services.runtime, line ~313) purely for its own
log-grouping purposes, predates distributed tracing entirely, and is never used to look
anything up in Tempo. The naming collision is unfortunate but `trace_id` was already taken
by that older, different concept - don't conflate the two when reading this table.

Nullable because every run before this column existed has none, and any run where
OTEL_EXPORTER_OTLP_ENDPOINT wasn't set (tracing is a no-op, see app/core/otel.py) has none
either - the console's Trace tab falls back to the existing logs.run_steps list for those.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("otel_trace_id", sa.String(length=32), nullable=True),
        schema="logs",
    )


def downgrade() -> None:
    op.drop_column("runs", "otel_trace_id", schema="logs")
