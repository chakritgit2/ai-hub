import uuid

from dynamiq.prompts import Message, MessageRole, Prompt
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, use_span

from app.core.db import get_company_session
from app.db.tables import run_steps_table
from app.services.guardrails import CheckEvent
from app.services.run_steps import RunStep, guardrail_run_steps, log_run_steps
from app.services.runs import log_run
from app.services.runtime import _StepCollector, _step_text, _wrap_memory_for_tracing

from .markers import requires_postgres

# Module-scoped real TracerProvider + InMemorySpanExporter, set once at collection time
# (before any test runs) - OTel only allows a global TracerProvider to be set once per
# process, so this is the one place in the whole ai/tests/ suite that does it. Every span
# _StepCollector/app.services.runtime._tracer create anywhere in this process (including
# the pre-existing tests above that call on_node_start directly, with no root span wrapped
# around them) now actually get recorded here instead of going to a no-op provider - those
# older tests don't assert on spans at all, so this is harmless for them.
_span_exporter = InMemorySpanExporter()
_test_tracer_provider = TracerProvider()
_test_tracer_provider.add_span_processor(SimpleSpanProcessor(_span_exporter))
trace.set_tracer_provider(_test_tracer_provider)


def _fake_span(trace_id: int) -> NonRecordingSpan:
    context = SpanContext(
        trace_id=trace_id,
        span_id=0x1,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return NonRecordingSpan(context)


def _node(group: str, name: str) -> dict:
    return {"group": group, "name": name, "type": f"dynamiq.nodes.{group}.{name}"}


def test_step_text_keeps_non_ascii_content_readable():
    """Found via a live run whose Thai-language LLM output rendered as unreadable
    `\\u0e09\\u0e31\\u0e19...` escapes in the console's Trace tab - json.dumps's default
    ensure_ascii=True was the cause, not a frontend/encoding bug."""
    text = _step_text({"content": "สวัสดีครับ"})

    assert text == '{"content": "สวัสดีครับ"}'
    assert "\\u" not in text


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


def test_step_collector_uses_the_llm_nodes_real_model_field_for_the_step_name():
    """A real code-review finding: dynamiq.nodes.llms.base.BaseLLM's node `name` is often
    a generic configured label (e.g. "LLM"), not the model it actually calls -
    `serialized["model"]` (a real BaseLLM field, confirmed via model_dump()) must win for
    LLM steps, since that's the only one of the two that's actually informative on the
    Trace tab pipeline/waterfall."""
    collector = _StepCollector()
    run_id = str(uuid.uuid4())
    node = {**_node("llms", "LLM"), "model": "gpt-4o-mini"}

    collector.on_node_start(node, {"input": "hi"}, run_id=run_id)
    collector.on_node_end(node, {"content": "hello"}, run_id=run_id)

    assert collector.steps[0].name == "gpt-4o-mini"


def test_step_collector_captures_the_real_prompt_messages_for_an_llm_step():
    """dynamiq's Agent always calls the LLM node with `input_data={}` - the real prompt
    rides along as a separate `prompt=` kwarg (dynamiq.nodes.agents.base.py's _run_llm),
    which `Runnable.run()` forwards straight through to on_node_start unchanged. Found via
    a live run showing Input: "{}" on an LLM step in the console's Trace tab - confirms the
    fix reads the real messages from `kwargs["prompt"]` instead of the always-empty
    `input_data`."""
    collector = _StepCollector()
    run_id = str(uuid.uuid4())
    prompt = Prompt(
        messages=[
            Message(role=MessageRole.SYSTEM, content="You are a helpful assistant."),
            Message(role=MessageRole.USER, content="who are you?"),
        ]
    )

    collector.on_node_start(_node("llms", "gpt-4o-mini"), {}, run_id=run_id, prompt=prompt)
    collector.on_node_end(_node("llms", "gpt-4o-mini"), {"content": "I am an assistant."}, run_id=run_id)

    [step] = collector.steps
    assert step.input_text != "{}"
    assert "who are you?" in step.input_text
    assert "You are a helpful assistant." in step.input_text


def test_step_collector_uses_input_data_for_an_llm_step_with_no_prompt_kwarg():
    """Backward-compat: a caller that doesn't pass `prompt=` (every pre-existing test in
    this file, and any future non-Agent caller of the LLM node) must keep getting
    input_data as-is, not blow up on a missing kwarg."""
    collector = _StepCollector()
    run_id = str(uuid.uuid4())

    collector.on_node_start(_node("llms", "gpt-4o-mini"), {"input": "hi"}, run_id=run_id)
    collector.on_node_end(_node("llms", "gpt-4o-mini"), {"content": "hello"}, run_id=run_id)

    assert collector.steps[0].input_text == '{"input": "hi"}'


def test_step_collector_falls_back_to_name_when_an_llm_node_has_no_model_field():
    collector = _StepCollector()
    run_id = str(uuid.uuid4())

    collector.on_node_start(_node("llms", "gpt-4o-mini"), {"input": "hi"}, run_id=run_id)
    collector.on_node_end(_node("llms", "gpt-4o-mini"), {"content": "hello"}, run_id=run_id)

    assert collector.steps[0].name == "gpt-4o-mini"


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


def test_step_collector_captures_the_real_otel_trace_id_from_the_active_span():
    """_StepCollector.otel_trace_id (PRD §6.9 Trace tab waterfall) must come from
    whatever OpenLIT's real OpenTelemetry span context is active when on_node_start fires
    - this is the only point in the run's control flow nested inside that span (see the
    class's own docstring)."""
    collector = _StepCollector()
    run_id = str(uuid.uuid4())
    trace_id = 0x1234567890ABCDEF1234567890ABCDEF

    with use_span(_fake_span(trace_id)):
        collector.on_node_start(_node("llms", "gpt-4o-mini"), {"input": "hi"}, run_id=run_id)

    assert collector.otel_trace_id == format(trace_id, "032x")


def test_step_collector_only_captures_the_otel_trace_id_once():
    """on_node_start fires once per node, not once per run - a later node (a different
    span in dynamiq's own per-node instrumentation) must not overwrite the run's actual
    root trace id captured from the first node."""
    collector = _StepCollector()
    first_run_id, second_run_id = str(uuid.uuid4()), str(uuid.uuid4())
    first_trace_id, second_trace_id = 0x1111, 0x2222

    with use_span(_fake_span(first_trace_id)):
        collector.on_node_start(_node("llms", "gpt-4o-mini"), {"input": "hi"}, run_id=first_run_id)
    with use_span(_fake_span(second_trace_id)):
        collector.on_node_start(_node("tools", "search_order"), {"query": "x"}, run_id=second_run_id)

    assert collector.otel_trace_id == format(first_trace_id, "032x")


def test_step_collector_leaves_otel_trace_id_none_when_tracing_is_a_noop():
    """No OTEL_EXPORTER_OTLP_ENDPOINT configured (app/core/otel.py) means openlit.init()
    never ran, so there's no real span active - get_span_context().is_valid is False in
    that case (the default NonRecordingSpan's all-zeros context), must not be captured as
    a fake trace id."""
    collector = _StepCollector()
    run_id = str(uuid.uuid4())

    collector.on_node_start(_node("llms", "gpt-4o-mini"), {"input": "hi"}, run_id=run_id)

    assert collector.otel_trace_id is None


def test_step_collector_creates_a_real_span_per_llm_step_with_gen_ai_attributes():
    """A real code-review finding (2026-11-05): OpenLIT's own litellm instrumentor
    silently fails to attach (app/core/otel.py's disabled_instrumentors comment), so
    _StepCollector creates these spans itself - attribute names follow OTel's GenAI
    semantic conventions, the same ones the console's Trace tab waterfall tooltip looks
    for (web/src/routes/runs/[id]/+page.svelte's spanAttribute() calls)."""
    _span_exporter.clear()
    collector = _StepCollector()
    run_id = str(uuid.uuid4())

    with trace.get_tracer(__name__).start_as_current_span("dynamiq.agent_run"):
        collector.on_node_start(_node("llms", "gpt-4o-mini"), {"input": "hi"}, run_id=run_id)
        collector.on_node_execute_run(
            _node("llms", "gpt-4o-mini"),
            run_id=run_id,
            usage_data={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens_cost_usd": 0.001},
        )
        collector.on_node_end(_node("llms", "gpt-4o-mini"), {"content": "hi"}, run_id=run_id)

    spans = {span.name: span for span in _span_exporter.get_finished_spans()}
    child = spans["gpt-4o-mini"]
    assert child.attributes["gen_ai.operation.name"] == "chat"
    assert child.attributes["gen_ai.request.model"] == "gpt-4o-mini"
    assert child.attributes["gen_ai.usage.input_tokens"] == 10
    assert child.attributes["gen_ai.usage.output_tokens"] == 5
    assert child.status.status_code == trace.StatusCode.OK
    # Parented to the root span, the way the real call site (_execute_agent_run_body)
    # wraps agent.run() in one - see runtime.py's own comment on why a context manager.
    assert child.parent.span_id == spans["dynamiq.agent_run"].context.span_id
    assert child.parent.trace_id == spans["dynamiq.agent_run"].context.trace_id


def test_step_collector_sets_a_tool_span_to_error_status_when_the_step_fails():
    _span_exporter.clear()
    collector = _StepCollector()
    run_id = str(uuid.uuid4())

    with trace.get_tracer(__name__).start_as_current_span("dynamiq.agent_run"):
        collector.on_node_start(_node("tools", "flaky_tool"), {"query": "x"}, run_id=run_id)
        collector.on_node_error(_node("tools", "flaky_tool"), RuntimeError("boom"), run_id=run_id)

    spans = {span.name: span for span in _span_exporter.get_finished_spans()}
    child = spans["flaky_tool"]
    assert child.attributes["gen_ai.operation.name"] == "execute_tool"
    assert child.status.status_code == trace.StatusCode.ERROR
    assert child.status.description == "boom"


def test_wrap_memory_for_tracing_records_a_memory_search_step_with_real_content():
    """Found via a live run showing no input/output at all on the "Memory search" card -
    Memory.search() isn't a dynamiq Node (see _wrap_memory_for_tracing's own docstring),
    so there's no on_node_start/on_node_end to read content from; this directly wraps the
    real Memory instance instead."""
    from dynamiq.memory.memory import InMemory, Memory

    memory = Memory(backend=InMemory())
    memory.add(role=MessageRole.USER, content="what's your return policy?")
    steps: list[RunStep] = []

    _wrap_memory_for_tracing(memory, steps)
    results = memory.search(query=None)

    assert len(results) == 1
    [step] = steps
    assert step.step_type == "memory_search"
    assert step.status == "success"
    assert "what's your return policy?" in step.output_text


def test_wrap_memory_for_tracing_distinguishes_user_and_assistant_saves_by_real_role():
    """A run's two "Memory save" cards come from dynamiq calling .add() once for the
    user's message and once for the agent's reply (base.py's _fallback_input_output_pair) -
    the real `role` on each call tells them apart deterministically, rather than the
    console frontend's own positional guess (first call = user, second = agent)."""
    from dynamiq.memory.memory import InMemory, Memory

    memory = Memory(backend=InMemory())
    steps: list[RunStep] = []
    _wrap_memory_for_tracing(memory, steps)

    memory.add(role=MessageRole.USER, content="what's your return policy?")
    memory.add(role=MessageRole.ASSISTANT, content="30 days, unused, with a receipt.")

    assert [step.name for step in steps] == ["Memory save (user message)", "Memory save (agent reply)"]
    assert all(step.step_type == "memory_add" and step.status == "success" for step in steps)


class _FakeReplaceMessagesMemory:
    """A minimal stand-in, not a real dynamiq Memory - the pydantic-bypass behavior
    `_wrap_memory_for_tracing` relies on for a real Memory is already covered by the two
    tests above; this one only needs to exercise `_wrap_memory_for_tracing`'s own
    success/NotImplementedError branching for replace_messages."""

    def __init__(self, *, supports_replace: bool):
        self._supports_replace = supports_replace

    def search(self, query=None, filters=None, limit=None):
        return []

    def add(self, role, content, metadata=None, tool_calls=None, tool_call_id=None, name=None):
        return None

    def replace_messages(self, filters, messages):
        if not self._supports_replace:
            raise NotImplementedError
        return None


def test_wrap_memory_for_tracing_records_one_save_step_when_replace_messages_succeeds():
    memory = _FakeReplaceMessagesMemory(supports_replace=True)
    steps: list[RunStep] = []
    _wrap_memory_for_tracing(memory, steps)

    memory.replace_messages(filters={"user_id": "u1"}, messages=[Message(role=MessageRole.USER, content="hi")])

    [step] = steps
    assert step.step_type == "memory_add"
    assert step.name == "Memory save"
    assert step.status == "success"
    assert "hi" in step.input_text


def test_wrap_memory_for_tracing_records_nothing_when_replace_messages_is_not_implemented():
    """Nothing was actually saved by a call that raised - the caller's own fallback to
    .add() (traced_add) is what will actually get recorded, not this call."""
    memory = _FakeReplaceMessagesMemory(supports_replace=False)
    steps: list[RunStep] = []
    _wrap_memory_for_tracing(memory, steps)

    try:
        memory.replace_messages(filters={"user_id": "u1"}, messages=[])
        raised = False
    except NotImplementedError:
        raised = True

    assert raised
    assert steps == []


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
