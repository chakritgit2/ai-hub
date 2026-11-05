import uuid

from app.core.db import get_company_session
from app.db.tables import runs_table
from app.services.runs import log_run

from .markers import requires_postgres


@requires_postgres
async def test_log_run_persists_otel_trace_id_when_given():
    company_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    await log_run(
        run_id=run_id,
        company_id=company_id,
        trace_id=str(uuid.uuid4()),
        otel_trace_id="1234567890abcdef1234567890abcdef",
        status="success",
    )

    async with get_company_session(company_id) as session:
        row = (
            (await session.execute(runs_table.select().where(runs_table.c.id == uuid.UUID(run_id))))
            .mappings()
            .one()
        )

    assert row["otel_trace_id"] == "1234567890abcdef1234567890abcdef"


@requires_postgres
async def test_log_run_persists_null_otel_trace_id_when_omitted():
    """Every run from before this column existed, and any run where tracing was a no-op
    (no OTEL_EXPORTER_OTLP_ENDPOINT), must leave this column NULL - not an error, not a
    placeholder value the console's Trace tab would mistake for a real trace."""
    company_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    await log_run(run_id=run_id, company_id=company_id, trace_id=str(uuid.uuid4()), status="success")

    async with get_company_session(company_id) as session:
        row = (
            (await session.execute(runs_table.select().where(runs_table.c.id == uuid.UUID(run_id))))
            .mappings()
            .one()
        )

    assert row["otel_trace_id"] is None
