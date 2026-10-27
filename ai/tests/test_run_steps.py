import uuid

from app.core.db import get_company_session
from app.db.tables import run_steps_table
from app.services.guardrails import CheckEvent
from app.services.run_steps import RunStep, guardrail_run_steps, log_run_steps
from app.services.runs import log_run
from app.services.runtime import _StepCollector

from .markers import requires_postgres


def _node(group: str, name: str) -> dict:
    return {"group": group, "name": name, "type": f"dynamiq.nodes.{group}.{name}"}


def test_step_collector_records_llm_then_tool_in_order():
    collector = _StepCollector()
    run_id_a, run_id_b = str(uuid.uuid4()), str(uuid.uuid4())

    collector.on_node_start(_node("llms", "gpt-4o-mini"), {"input": "hi"}, run_id=run_id_a)
    collector.on_node_execute_run(_node("llms", "gpt-4o-mini"), run_id=run_id_a, usage_data={
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens_cost_usd": 0.001,
    })
    collector.on_node_end(_node("llms", "gpt-4o-mini"), {"content": "thinking"}, run_id=run_id_a)

    collector.on_node_start(_node("tools", "search_order"), {"query": "123"}, run_id=run_id_b)
    collector.on_node_end(_node("tools", "search_order"), {"result": "found"}, run_id=run_id_b)

    assert [step.step_type for step in collector.steps] == ["llm", "tool_call"]
    llm_step, tool_step = collector.steps
    assert llm_step.name == "gpt-4o-mini"
    assert llm_step.status == "success"
    assert llm_step.tokens_in == 10
    assert llm_step.tokens_out == 5
    assert llm_step.cost_usd == 0.001
    assert tool_step.name == "search_order"
    assert tool_step.tokens_in is None


def test_step_collector_skips_the_top_level_agent_node():
    collector = _StepCollector()
    agent_run_id = str(uuid.uuid4())
    collector.on_node_start(_node("agents", "my-agent"), {"input": "hi"}, run_id=agent_run_id)
    collector.on_node_end(_node("agents", "my-agent"), {"content": "done"}, run_id=agent_run_id)

    assert collector.steps == []


def test_step_collector_records_error_status():
    collector = _StepCollector()
    tool_run_id = str(uuid.uuid4())
    collector.on_node_start(_node("tools", "flaky_tool"), {"query": "x"}, run_id=tool_run_id)
    collector.on_node_error(_node("tools", "flaky_tool"), RuntimeError("boom"), run_id=tool_run_id)

    assert len(collector.steps) == 1
    assert collector.steps[0].status == "error"
    assert collector.steps[0].error == "boom"


def test_step_collector_marks_fallback_llm_step_in_detail():
    """PRD §6.1/§11: once the primary LLM fails and dynamiq's native FallbackConfig runs
    the fallback LLM, that second node's own serialized form carries `is_fallback: True`
    (dynamiq.nodes.llms.base.BaseLLM.to_dict) - _StepCollector must forward it into the
    step's `detail` so the Trace tab can show which model actually answered."""
    collector = _StepCollector()
    primary_run_id, fallback_run_id = str(uuid.uuid4()), str(uuid.uuid4())

    primary_node = _node("llms", "gpt-4o-mini")
    collector.on_node_start(primary_node, {"input": "hi"}, run_id=primary_run_id)
    collector.on_node_error(primary_node, RuntimeError("503 service unavailable"), run_id=primary_run_id)

    fallback_node = {**_node("llms", "gpt-4o"), "is_fallback": True}
    collector.on_node_start(fallback_node, {"input": "hi"}, run_id=fallback_run_id)
    collector.on_node_end(fallback_node, {"content": "hello"}, run_id=fallback_run_id)

    assert [step.status for step in collector.steps] == ["error", "success"]
    primary_step, fallback_step = collector.steps
    assert primary_step.detail == {}
    assert fallback_step.detail == {"is_fallback": True}


def test_guardrail_run_steps_converts_check_events():
    events = [CheckEvent(stage="input", check="pii", action="mask", detail={"categories": ["email"]})]

    steps = guardrail_run_steps(events, "guardrail_input")

    assert len(steps) == 1
    assert steps[0].step_type == "guardrail_input"
    assert steps[0].name == "pii"
    assert steps[0].status == "mask"
    assert steps[0].detail == {"categories": ["email"]}


async def test_log_run_steps_is_a_noop_for_empty_steps():
    # No company_id/steps means no DB round trip at all.
    await log_run_steps("", run_id=str(uuid.uuid4()), steps=[])


@requires_postgres
async def test_log_run_steps_writes_rows_in_order_scoped_by_company():
    company_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    await log_run(run_id=run_id, company_id=company_id, trace_id=str(uuid.uuid4()), status="success")

    steps = [
        RunStep(step_type="llm", name="gpt-4o-mini", status="success", tokens_in=10, tokens_out=5),
        RunStep(step_type="tool_call", name="search_order", status="success"),
    ]

    try:
        await log_run_steps(company_id, run_id=run_id, steps=steps)

        async with get_company_session(company_id) as session:
            rows = (
                (
                    await session.execute(
                        run_steps_table.select()
                        .where(run_steps_table.c.run_id == uuid.UUID(run_id))
                        .order_by(run_steps_table.c.step_index)
                    )
                )
                .mappings()
                .all()
            )

        assert [row["step_type"] for row in rows] == ["llm", "tool_call"]
        assert [row["step_index"] for row in rows] == [0, 1]
        assert rows[0]["name"] == "gpt-4o-mini"
    finally:
        async with get_company_session(company_id) as session:
            await session.execute(
                run_steps_table.delete().where(run_steps_table.c.company_id == uuid.UUID(company_id))
            )
