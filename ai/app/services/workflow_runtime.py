"""Workflow run execution (PRD §6.9, phase 4, slice 2) - the workflow-shaped sibling of
`app.services.runtime`'s single-agent execution. Resolves a company's own workflow
version, compiles its *current* (possibly draft) spec live - same "Playground tests
drafts too" reasoning `runtime.resolve_and_compile` already documents for a lone agent -
then builds a real `GraphOrchestrator` from real decrypted secrets and runs it.

Deliberately a new, self-contained module rather than a refactor of `app.services.runtime`
- lower risk (zero behavior change to the already-tested, already-deployed single-agent
execution path), consistent with this project's established preference for small new
siblings over touching shared code (e.g. `resolve_published_agent_version_for_workflow`
added instead of changing `resolve_agent_version`).

No persisted conversation memory and no input/output guardrails in this slice (every
state's Agent is built with `memory=None`; `WorkflowSpecDoc` has no `guardrails` field at
all) - a workflow run is one-shot input -> final output, not yet wired into either
concept. No delegated end-user identity either (`identity=None` per state) - an
`auth_mode: delegated` HTTP tool inside a state's agent fails closed at call time (the
same fail-closed behavior a missing identity already has in `compiler.build_agent`),
since there's no Playground-sourced end-user identity concept for workflow runs yet.

`run_deployment_workflow` (PRD §6.3/§6.9 phase 4 workflow deployment exposure) is the
workflow-shaped sibling of `app.services.runtime.run_deployment_agent` - same rate-limit/
quota-then-run-then-reconcile shape, but deliberately does not pre-reserve quota (see its
own docstring for why) and has no guardrail_overrides merge to perform (nothing in a
compiled workflow definition to merge them into yet).
"""
import asyncio
import time
from uuid import uuid4

from dynamiq.runnables.base import RunnableConfig, RunnableStatus

from app.core.config import get_settings
from app.integrations.skill_registry import ConsoleSkillRegistry, get_console_skill_registry
from app.services.compiler import BUILTIN_TOOL_TYPES, RETRIEVAL_TOOL_TYPE, build_agent
from app.services.connection_secrets import decrypt_connection_secret
from app.services.deployments import resolve_deployment
from app.services.egress_allowlist import list_egress_allowlist
from app.services.quotas import QuotaExceededError, check_quota_not_exceeded, check_rate_limit, record_actual_usage
from app.services.runs import RunUsage, log_run

# Reused as-is rather than duplicated - a small, pure usage-aggregation utility with no
# workflow-specific assumptions (see runtime.py's own docstring on how it threads through
# RunnableConfig.callbacks); duplicating it would risk the two copies drifting apart on
# how tokens/cost are summed.
from app.services.runtime import _UsageCollector
from app.services.tool_secrets import decrypt_tool_secret
from app.services.workflow_compiler import build_manager, compile_workflow_spec, wire_orchestrator_states
from app.services.workflow_versions import resolve_workflow_version


async def resolve_and_compile_workflow(company_id: str, workflow_version_id: str, role: str) -> dict:
    """Mirrors `app.services.runtime.resolve_and_compile` exactly, for a workflow version
    - see that function's own docstring for why "always compile the current, possibly-
    draft spec live" is the right behavior here too (a workflow version's own
    `is_published` status is not a precondition for testing it in Playground - only each
    STATE's referenced agent must be published, already enforced inside
    `compile_workflow_spec` itself)."""
    version = await resolve_workflow_version(company_id, workflow_version_id)
    if version is None:
        raise ValueError(f"workflow_version_id {workflow_version_id!r} not found for this company")

    result = await compile_workflow_spec(version.spec, role, company_id)
    if not result["ok"]:
        raise ValueError(f"workflow version {workflow_version_id!r} failed to compile: {result['errors']}")

    return result["compiled_definition"]


async def _resolve_state_agent(agent_compiled: dict, company_id: str):
    """Replicates, for one workflow state's embedded agent `compiled_definition`, the
    same live secret/skill/egress resolution `runtime.execute_agent_run` already does for
    a lone agent (its steps for connection secret, skill registry scoping, egress
    allowlist, knowledge-base secrets, and tool secrets) - inlined here rather than
    shared, since secret-handling logic is exactly the kind of code this project prefers
    to keep duplicated-but-simple over abstracted-but-riskier (see this module's own
    docstring)."""
    agent_def = agent_compiled["agent"]
    connection_id = agent_def["llm"]["connection"]["connection_id"]

    secret = await decrypt_connection_secret(company_id, connection_id)
    if secret is None:
        raise ValueError(f"no secret stored for connection {connection_id!r}")

    skill_registry = None
    if agent_compiled.get("skills"):
        full_registry = await get_console_skill_registry(company_id)
        scoped_skills = [s for s in full_registry.skills if s.name in agent_compiled["skills"]]
        skill_registry = ConsoleSkillRegistry(skills=scoped_skills)

    egress_allowlist = None
    if agent_def["llm"]["connection"].get("api_base"):
        egress_allowlist = await list_egress_allowlist(company_id)

    knowledge_api_keys = {}
    for tool_def in agent_def.get("tools", []):
        if tool_def["type"] != RETRIEVAL_TOOL_TYPE:
            continue
        embedder_connection_id = tool_def["config"]["embedder_connection_id"]
        kb_secret = await decrypt_connection_secret(company_id, embedder_connection_id)
        if kb_secret is None:
            raise ValueError(f"no secret stored for connection {embedder_connection_id!r}")
        knowledge_api_keys[tool_def["id"]] = kb_secret

    tool_secrets = {}
    for tool_def in agent_def.get("tools", []):
        if tool_def["type"] in BUILTIN_TOOL_TYPES:
            tool_secret = await decrypt_tool_secret(company_id, tool_def["id"])
            if tool_secret is None:
                raise ValueError(f"no secret stored for builtin tool {tool_def['name']!r}")
            tool_secrets[tool_def["id"]] = tool_secret
            continue
        if tool_def.get("auth_mode") != "service":
            continue
        tool_secret = await decrypt_tool_secret(company_id, tool_def["id"])
        if tool_secret is not None:
            tool_secrets[tool_def["id"]] = tool_secret

    return await build_agent(
        agent_compiled,
        secret,
        company_id,
        knowledge_api_keys=knowledge_api_keys,
        tool_secrets=tool_secrets,
        skill_registry=skill_registry,
        egress_allowlist=egress_allowlist,
    )


async def execute_workflow_run(
    company_id: str,
    workflow_version_id: str,
    role: str,
    input_text: str,
    source: str = "playground",
    deployment_id: str | None = None,
) -> dict:
    """Resolves `workflow_version_id`, compiles it live, builds a real `GraphOrchestrator`
    from real decrypted secrets (manager + every state's agent), and runs it.
    `deployment_id` is `None` for Playground calls (the default) and the real deployment id
    for a gateway call via `run_deployment_workflow`, threaded into `logs.runs` exactly
    like `runtime.execute_agent_run`'s own `deployment_id` param.

    Returns `{"output", "trace_id", "latency_ms", "tokens_in", "tokens_out", "cost_usd"}`
    - no `conversation_id` (no memory this slice).
    """
    from dynamiq.nodes.agents.orchestrators.graph import GraphOrchestrator

    run_id = str(uuid4())
    trace_id = str(uuid4())
    started = time.monotonic()

    # Resolution/compile/secret failures here are NOT logged to logs.runs - same
    # convention runtime.py's own resolve_and_compile/pre-build_agent steps already
    # follow (they raise straight to the caller, only the actual run attempt below gets
    # a logged row on failure).
    compiled_definition = await resolve_and_compile_workflow(company_id, workflow_version_id, role)
    workflow_def = compiled_definition["workflow"]

    manager_connection_id = workflow_def["manager"]["connection"]["connection_id"]
    manager_secret = await decrypt_connection_secret(company_id, manager_connection_id)
    if manager_secret is None:
        raise ValueError(f"no secret stored for connection {manager_connection_id!r}")
    manager = build_manager(workflow_def["manager"], manager_secret)

    orchestrator = GraphOrchestrator(
        manager=manager,
        initial_state=workflow_def["initial_state"],
        max_loops=workflow_def["max_loops"],
    )

    agents_by_state_id = {}
    for state_def in workflow_def["states"]:
        agents_by_state_id[state_def["id"]] = await _resolve_state_agent(
            state_def["agent_compiled_definition"], company_id
        )
    wire_orchestrator_states(orchestrator, workflow_def, agents_by_state_id)

    usage_collector = _UsageCollector()

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                orchestrator.run,
                input_data={"input": input_text},
                config=RunnableConfig(callbacks=[usage_collector]),
            ),
            timeout=get_settings().RUN_TIMEOUT_SECONDS,
        )
        if result.status != RunnableStatus.SUCCESS:
            message = result.error.message if result.error else f"workflow run ended with status {result.status}"
            raise RuntimeError(message)
        # `run_flow` falls off the end with an implicit `None` if `max_loops` is
        # exhausted before reaching END - still a SUCCESS RunnableResult, so this must be
        # checked explicitly rather than trusting `status == SUCCESS` alone (confirmed
        # against the installed dynamiq==0.65.0 source - a lone Agent run has no
        # equivalent gap).
        if not result.output or result.output.get("content") is None:
            raise RuntimeError("workflow did not reach a final state within max_loops")
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        usage = usage_collector.total_usage()
        # Lets a deployment caller (run_deployment_workflow) record real usage from a run
        # that failed partway through, same convention runtime.execute_agent_run already
        # attaches `partial_usage` for.
        exc.partial_usage = usage
        await log_run(
            run_id=run_id,
            company_id=company_id,
            trace_id=trace_id,
            status="error",
            source=source,
            workflow_version_id=workflow_version_id,
            deployment_id=deployment_id,
            usage=usage,
            latency_ms=latency_ms,
            input_text=input_text,
            error=str(exc),
        )
        raise

    output = result.output["content"]
    latency_ms = int((time.monotonic() - started) * 1000)
    usage = usage_collector.total_usage()

    await log_run(
        run_id=run_id,
        company_id=company_id,
        trace_id=trace_id,
        status="success",
        source=source,
        workflow_version_id=workflow_version_id,
        deployment_id=deployment_id,
        usage=usage,
        latency_ms=latency_ms,
        input_text=input_text,
        output_text=output,
    )

    return {
        "output": output,
        "trace_id": trace_id,
        "latency_ms": latency_ms,
        "tokens_in": usage.tokens_in,
        "tokens_out": usage.tokens_out,
        "cost_usd": usage.cost_usd,
    }


async def run_deployment_workflow(
    company_id: str,
    deployment_id: str,
    role: str,
    input_text: str,
    external_user_id: str | None = None,
    claims: dict | None = None,
) -> dict:
    """The workflow-shaped sibling of `app.services.runtime.run_deployment_agent` - the
    gateway's counterpart to Playground's `execute_workflow_run`. Unlike the agent path,
    does NOT pre-reserve quota: `reserve_quota` needs one `model`/`max_tokens` pair to size
    an estimate, but a workflow run uses one model per state plus a manager, so there's no
    single honest number to reserve against (see `check_quota_not_exceeded`'s own docstring
    for the accepted tradeoff). There is also no guardrail_overrides merge to perform -
    `execute_workflow_run` doesn't build a `guardrails` argument at all yet (no guardrails
    concept exists for workflows this slice).

    Raises `ValueError` for a missing/disabled/non-workflow deployment and
    `QuotaExceededError` when rate-limited/over quota, same conventions as
    `run_deployment_agent`.
    """
    deployment = await resolve_deployment(company_id, deployment_id)
    if deployment is None:
        raise ValueError(f"deployment_id {deployment_id!r} not found for this company")
    if not deployment.enabled:
        raise ValueError(f"deployment_id {deployment_id!r} is disabled")
    if deployment.workflow_version_id is None:
        raise ValueError(f"deployment_id {deployment_id!r} is not a workflow deployment")

    rate_limit = await check_rate_limit(deployment_id, deployment.rate_limit_per_min)
    if not rate_limit.allowed:
        raise QuotaExceededError("rate_limit", reset_at=None)

    quota_check = await check_quota_not_exceeded(
        deployment_id, deployment.daily_token_limit, deployment.daily_cost_limit_usd
    )
    if not quota_check.allowed:
        raise QuotaExceededError(quota_check.reason or "quota_exceeded", reset_at=quota_check.reset_at)

    result: dict | None = None
    partial_usage: RunUsage | None = None
    try:
        result = await execute_workflow_run(
            company_id,
            deployment.workflow_version_id,
            role,
            input_text,
            source="api",
            deployment_id=deployment_id,
        )
        return result
    except Exception as exc:
        partial_usage = getattr(exc, "partial_usage", None)
        raise
    finally:
        # quota_check.today is None when the deployment has no limits configured at all
        # (check_quota_not_exceeded never touched Redis in that case) - nothing to record.
        if quota_check.today is not None and quota_check.ttl is not None:
            if result is not None:
                actual_tokens = (result.get("tokens_in") or 0) + (result.get("tokens_out") or 0)
                actual_cost_usd = result.get("cost_usd") or 0
            elif partial_usage is not None:
                actual_tokens = (partial_usage.tokens_in or 0) + (partial_usage.tokens_out or 0)
                actual_cost_usd = partial_usage.cost_usd or 0
            else:
                actual_tokens = 0
                actual_cost_usd = 0
            await record_actual_usage(
                deployment_id, actual_tokens, actual_cost_usd, quota_check.today, quota_check.ttl
            )
