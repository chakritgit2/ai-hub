<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import {
		getRun,
		getRunContent,
		getRunConversation,
		getRunTrace,
		listRunGuardrailEvents,
		listRunSteps,
		replayRun
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type {
		GuardrailEvent,
		Run,
		RunContent,
		RunConversation,
		RunStatus,
		RunStep,
		RunTrace,
		TraceSpan
	} from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { currentUser, roleForCompany } from '$lib/stores/auth';

	type TabId = 'trace' | 'conversation' | 'guardrail';

	let run = $state<Run | null>(null);
	let replaying = $state(false);
	let replayError = $state<string | null>(null);

	// Same write-role boundary replayRun() enforces server-side (PRD §7.2: viewer is
	// read-only) - this just hides the button for a role that would get a 403 anyway.
	// agent_version_id is null for a workflow run (the API's Run type claims non-null, but
	// the DB column is nullable and the JSON really does carry null here) - replayRun()
	// 404s on those, so the button stays disabled rather than offering a guaranteed failure.
	let canReplay = $derived(
		run !== null &&
			!!run.agent_version_id &&
			['admin', 'developer'].includes(roleForCompany($currentUser, run.company_id) ?? '')
	);
	// Same write-role boundary as canReplay, but without the agent_version_id requirement -
	// getRunContent() (unlike replayRun()) works for workflow runs too, since viewing
	// content has nothing to do with Playground's agent-only replay.
	let canViewContent = $derived(
		run !== null && ['admin', 'developer'].includes(roleForCompany($currentUser, run.company_id) ?? '')
	);
	let content = $state<RunContent | null>(null);
	let contentLoading = $state(false);
	let contentError = $state<string | null>(null);

	async function loadContent() {
		if (!run || contentLoading) return;
		contentLoading = true;
		contentError = null;
		try {
			content = await getRunContent(run.id);
		} catch (e) {
			contentError =
				e instanceof ApiError && e.status === 403
					? 'Viewing run content needs the admin or developer role.'
					: e instanceof Error
						? e.message
						: 'Failed to load this run\'s content.';
		} finally {
			contentLoading = false;
		}
	}

	let steps = $state<RunStep[]>([]);
	let stepsError = $state<string | null>(null);
	let trace = $state<RunTrace['trace']>(null);
	let conversation = $state<RunConversation | null>(null);
	let conversationError = $state<string | null>(null);
	let guardrailEvents = $state<GuardrailEvent[]>([]);
	let guardrailError = $state<string | null>(null);
	let guardrailLoading = $state(true);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let activeTab = $state<TabId>('trace');

	const STEP_TYPE_LABELS: Record<RunStep['step_type'], string> = {
		guardrail_input: 'Guardrail (input)',
		llm: 'LLM call',
		tool_call: 'Tool call',
		guardrail_output: 'Guardrail (output)'
	};

	// Plain-language, one-line "what is this" for each step type - shown as a tooltip on
	// the stage card and as a subtitle in the detail panel, so the pipeline reads as a
	// narrative of what the agent actually did, not just a row of jargon labels.
	const STEP_TYPE_DESCRIPTIONS: Record<RunStep['step_type'], string> = {
		guardrail_input: 'Checks your message against the configured safety rules.',
		llm: 'Asks the model to think and generate the next part of the response.',
		tool_call: 'Calls an external tool to get information or take an action.',
		guardrail_output: "Checks the agent's reply against the configured safety rules."
	};

	const GUARDRAIL_ACTION_LABELS: Record<GuardrailEvent['action'], string> = {
		block: 'Blocked',
		mask: 'Masked',
		flag: 'Flagged'
	};

	function spanAttribute(span: TraceSpan, key: string): string | null {
		const value = span.attributes[key];
		return value === undefined ? null : String(value);
	}

	// --- Step pipeline (primary Trace tab view) -----------------------------------------
	// Normalizes two different data sources into one shared shape the template renders
	// once: `trace.spans` (Tempo, curated - see pipelineStepsFromTrace below) when a real
	// OTel trace exists, falling back to `steps` (logs.run_steps via listRunSteps) for
	// every run that predates this trace plumbing or where Tempo has nothing. The Tempo
	// path shows more of the real flow (memory search/save aren't `Node`s in dynamiq, so
	// they never fire the on_node_start/on_node_end callbacks _StepCollector listens to -
	// logs.run_steps structurally can't include them) at the cost of depending on
	// OpenLIT's own span shape for the curation filter below, which is somewhat fragile
	// (the same OpenLIT whose litellm instrumentor silently broke once already) - the
	// run_steps fallback has no such dependency, which is why it stays the baseline.
	interface PipelineStep {
		key: string;
		typeLabel: string;
		name: string;
		isFailure: boolean;
		statusLabel: string;
		latencyMs: number | null;
		tokensIn: number | null;
		tokensOut: number | null;
		costUsd: number | null;
		errorText: string | null;
		isFallbackModel: boolean;
		// Already returned by listRunSteps() to any member today (RunsController::
		// formatRunStep - no write-role gate, unlike the run-level input/output behind
		// "Show input/output" above), just never rendered until now. Tempo spans never
		// carry message content themselves (capture_message_content=False, ai/app/core/
		// otel.py) - for a Tempo-sourced LLM/tool step, this is backfilled from the
		// matching run_steps row instead (see pipelineStepsFromTrace); stays null for a
		// Memory step, which has no run_steps row to backfill from at all.
		stepInput: string | null;
		stepOutput: string | null;
		// Plain-language "what is this step" - see STEP_TYPE_DESCRIPTIONS above.
		description: string;
	}

	function pipelineStepsFromRunSteps(runSteps: RunStep[]): PipelineStep[] {
		return runSteps.map((step) => ({
			key: step.id,
			typeLabel: STEP_TYPE_LABELS[step.step_type],
			name: step.name ?? '—',
			isFailure: step.status === 'error' || step.status === 'block',
			statusLabel: step.status,
			latencyMs: step.latency_ms,
			tokensIn: step.tokens_in,
			tokensOut: step.tokens_out,
			costUsd: step.cost_usd,
			errorText: step.error,
			isFallbackModel: Boolean(step.detail?.is_fallback),
			stepInput: step.input,
			stepOutput: step.output,
			description: STEP_TYPE_DESCRIPTIONS[step.step_type]
		}));
	}

	const TEMPO_STEP_TYPE_LABELS: Record<string, string> = {
		chat: 'LLM call',
		execute_tool: 'Tool call',
		invoke_agent: 'Memory'
	};

	// A span counts as a pipeline step when it's one WE created (gen_ai.operation.name
	// "chat"/"execute_tool", see ai/app/services/runtime.py's _StepCollector) or one of
	// OpenLIT's own dynamiq-level spans ("invoke_agent") - excluding the run's own root
	// span itself (dynamiq.agent_run carries "invoke_agent" too, but it's the container
	// the whole trace lives under, not a step within it). Everything else Tempo returns
	// (raw Postgres QUERY/COMMIT spans, the raw HTTP POST to the LLM provider) duplicates
	// what a kept span already represents at a level a human reading this page cares
	// about, so it's left out here - still visible in the unfiltered "full trace" expand.
	function isPipelineSpan(span: TraceSpan): boolean {
		if (span.name === 'dynamiq.agent_run') return false;
		const op = span.attributes['gen_ai.operation.name'];
		return op === 'chat' || op === 'execute_tool' || op === 'invoke_agent';
	}

	// Tempo spans never carry input/output text (capture_message_content=False), but the
	// LLM/tool_call spans this page keeps DO have a real counterpart in `run_steps` - the
	// exact same _StepCollector.on_node_start/_finish cycle (ai/app/services/runtime.py)
	// creates both the Tempo span and the RunStep row for every LLM/tool call, in the same
	// order, so the Nth curated "chat"/"execute_tool" span and the Nth run_steps row of
	// that same type are guaranteed to be the same underlying call - not a fuzzy guess.
	// Memory steps have no run_steps counterpart at all (Memory.search()/add() aren't
	// dynamiq Nodes, see pipelineStepsFromRunSteps's own comment) - input/output stays
	// null for those, there is genuinely nothing recorded to show.
	function runStepInputOutputByPosition(runSteps: RunStep[]): Record<'llm' | 'tool_call', RunStep[]> {
		const byType: Record<'llm' | 'tool_call', RunStep[]> = { llm: [], tool_call: [] };
		for (const step of runSteps) {
			if (step.step_type === 'llm' || step.step_type === 'tool_call') byType[step.step_type].push(step);
		}
		return byType;
	}

	function pipelineStepsFromTrace(spans: TraceSpan[], runSteps: RunStep[]): PipelineStep[] {
		const runStepsByType = runStepInputOutputByPosition(runSteps);
		const nextIndex: Record<'llm' | 'tool_call', number> = { llm: 0, tool_call: 0 };
		// dynamiq saves the user's message to memory first, then the agent's reply, as two
		// separate memory.add() calls, whenever the memory backend doesn't support an atomic
		// "replace" (ai/.venv/.../dynamiq/nodes/agents/base.py's _fallback_input_output_pair
		// builds [user_message, assistant_message] in that fixed order, then loops over it) -
		// the ONLY reason a run shows two "Memory save" cards. Same order every time, so the
		// Nth dynamiq.memory_add span in start-time order reliably tells us which one it is.
		let memorySaveCount = 0;

		return spans
			.filter(isPipelineSpan)
			.slice()
			.sort((a, b) => a.start_ms_relative - b.start_ms_relative)
			.map((span) => {
				const op = String(span.attributes['gen_ai.operation.name'] ?? '');
				const isFailure = span.status === 'error';
				const runStepType = op === 'chat' ? 'llm' : op === 'execute_tool' ? 'tool_call' : null;
				const matchedRunStep = runStepType ? runStepsByType[runStepType][nextIndex[runStepType]++] : undefined;

				let name: string;
				let description: string;
				if (op === 'chat') {
					name = String(spanAttribute(span, 'gen_ai.request.model') ?? span.name);
					description = STEP_TYPE_DESCRIPTIONS.llm;
				} else if (op === 'execute_tool') {
					name = span.name;
					description = STEP_TYPE_DESCRIPTIONS.tool_call;
				} else if (span.name === 'dynamiq.memory_search') {
					name = 'Memory search';
					description = 'Looks up anything relevant from earlier turns in this conversation.';
				} else if (span.name === 'dynamiq.memory_add') {
					const isFirst = memorySaveCount === 0;
					memorySaveCount++;
					name = isFirst ? 'Memory save (user message)' : 'Memory save (agent reply)';
					description = isFirst
						? 'Saves your message to memory, so later turns can refer back to it.'
						: "Saves the agent's reply to memory, so later turns can refer back to it.";
				} else {
					name = span.name;
					description = 'A step in the agent run.';
				}

				// Tempo only carries what we put on the span (status, gen_ai.usage.*) - cost,
				// the error message text, and the fallback-model flag were never attributes
				// we set (see _StepCollector in runtime.py), so without this backfill a
				// failed Tempo-sourced step would show as "error" with no explanation why.
				// matchedRunStep is the exact same underlying call (see the comment on
				// runStepInputOutputByPosition above), so this is a real value, not a guess.
				return {
					key: span.span_id,
					typeLabel: TEMPO_STEP_TYPE_LABELS[op] ?? 'Step',
					name,
					description,
					isFailure,
					statusLabel: isFailure ? 'error' : 'success',
					latencyMs: Math.round(span.duration_ms),
					tokensIn:
						(typeof span.attributes['gen_ai.usage.input_tokens'] === 'number'
							? span.attributes['gen_ai.usage.input_tokens']
							: null) ?? matchedRunStep?.tokens_in ?? null,
					tokensOut:
						(typeof span.attributes['gen_ai.usage.output_tokens'] === 'number'
							? span.attributes['gen_ai.usage.output_tokens']
							: null) ?? matchedRunStep?.tokens_out ?? null,
					costUsd: matchedRunStep?.cost_usd ?? null,
					errorText: matchedRunStep?.error ?? null,
					isFallbackModel: Boolean(matchedRunStep?.detail?.is_fallback),
					stepInput: matchedRunStep?.input ?? null,
					stepOutput: matchedRunStep?.output ?? null
				};
			});
	}

	// Guardrail checks aren't dynamiq Nodes either (they run in app/services/guardrails.py,
	// around agent.run() - see runtime.py's _execute_agent_run_body), so like memory
	// search/save they never fire on_node_start and never get a Tempo span. Unlike memory,
	// there's no Tempo-side signal for them at all - the only record is their run_steps
	// rows. Reuses pipelineStepsFromRunSteps's per-type description/label mapping so a
	// guardrail card looks the same whether the pipeline came from Tempo or the fallback.
	function guardrailPipelineSteps(runSteps: RunStep[], stepType: 'guardrail_input' | 'guardrail_output'): PipelineStep[] {
		return pipelineStepsFromRunSteps(runSteps.filter((step) => step.step_type === stepType));
	}

	// Synthetic - not a real logs.run_steps row or Tempo span, just marks where the flow
	// actually begins (the user's/API caller's own message) so the pipeline doesn't open
	// cold on "Memory search"/"LLM call" with no visible starting point. No duration (the
	// clock starts here), no expandable input/output (that's already one click away via
	// the "Show input/output" button above - PRD PII reasoning, see replayRun()'s
	// docblock - not duplicated per-step for a thing that isn't really a step).
	const START_STEP: PipelineStep = {
		key: '__start__',
		typeLabel: 'Start',
		name: 'Message received',
		description: 'Your message arrives and the agent starts working on a reply.',
		isFailure: false,
		statusLabel: 'received',
		latencyMs: null,
		tokensIn: null,
		tokensOut: null,
		costUsd: null,
		errorText: null,
		isFallbackModel: false,
		stepInput: null,
		stepOutput: null
	};

	// Closes the narrative the START_STEP opens - mirrors a Jenkins pipeline's final stage,
	// so the row always reads start-to-finish instead of trailing off after the last real
	// step. Labeled from the run's own status rather than always "done", since a run can
	// end in several ways (guardrail block, error, cancel) and that's exactly what a reader
	// scanning left-to-right wants to see without opening the run's own status badge.
	const END_STEP_BY_STATUS: Record<RunStatus, { name: string; description: string }> = {
		success: { name: 'Reply sent', description: 'The reply is finalized and sent back.' },
		error: { name: 'Run failed', description: 'The run hit an error before it could finish.' },
		blocked: { name: 'Blocked', description: 'A guardrail blocked the run before it could finish.' },
		interrupted: { name: 'Interrupted', description: 'The run was interrupted before it could finish.' },
		truncated: { name: 'Truncated', description: 'The run was truncated before it could finish.' },
		cancelled: { name: 'Cancelled', description: 'The run was cancelled before it could finish.' }
	};

	function endStepFor(status: RunStatus): PipelineStep {
		const { name, description } = END_STEP_BY_STATUS[status];
		return {
			key: '__end__',
			typeLabel: 'End',
			name,
			description,
			isFailure: status !== 'success',
			statusLabel: status,
			latencyMs: null,
			tokensIn: null,
			tokensOut: null,
			costUsd: null,
			errorText: null,
			isFallbackModel: false,
			stepInput: null,
			stepOutput: null
		};
	}

	let usingTraceSource = $derived(trace !== null && trace.spans.length > 0);
	// Input guardrail checks run before agent.run() starts, output checks run after it
	// finishes (runtime.py's _execute_agent_run_body) - a fixed position, not a guess, so
	// they're spliced onto the front/back of the Tempo-sourced steps rather than sorted in
	// by a timestamp Tempo never recorded for them. The run_steps fallback already carries
	// them inline in the right place and needs no splicing.
	let realPipelineSteps = $derived<PipelineStep[]>(
		usingTraceSource && trace
			? [
					...guardrailPipelineSteps(steps, 'guardrail_input'),
					...pipelineStepsFromTrace(trace.spans, steps),
					...guardrailPipelineSteps(steps, 'guardrail_output')
				]
			: pipelineStepsFromRunSteps(steps)
	);
	let pipelineSteps = $derived<PipelineStep[]>(
		realPipelineSteps.length > 0 && run
			? [START_STEP, ...realPipelineSteps, endStepFor(run.status)]
			: []
	);
	let selectedStepKey = $state<string | null>(null);
	let selectedStep = $derived(pipelineSteps.find((s) => s.key === selectedStepKey) ?? null);

	// Transient "Copied" feedback on the Input/Output copy buttons below - keyed by
	// `${step.key}-input`/`-output` rather than a single boolean so switching steps or
	// copying the other field doesn't show stale "Copied" text on the wrong button.
	let copiedKey = $state<string | null>(null);

	async function copyStepText(key: string, text: string) {
		try {
			await navigator.clipboard.writeText(text);
			copiedKey = key;
			setTimeout(() => {
				if (copiedKey === key) copiedKey = null;
			}, 1500);
		} catch {
			// Clipboard API can be unavailable (insecure context, permissions denied) - the
			// text is already visible/selectable in the <pre> block either way, so no-op.
		}
	}

	let maxStepLatencyMs = $derived(Math.max(1, ...pipelineSteps.map((s) => s.latencyMs ?? 0)));

	// Relative to the slowest step in this run, not the OTel waterfall's absolute-time
	// scale - a tool call that waited minutes on human approval must not shrink every other
	// step's bar down to an invisible sliver. Floored at 4% so even a near-zero-latency
	// step still shows a visible bar, not an empty track.
	function durationBarWidthPct(latencyMs: number): number {
		return Math.max(4, (latencyMs / maxStepLatencyMs) * 100);
	}

	async function load(id: string) {
		loading = true;
		error = null;
		selectedStepKey = null;
		try {
			run = await getRun(id);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load this run.';
		} finally {
			loading = false;
		}

		if (run === null) return;

		const hasConversation = run.conversation_id !== null && run.conversation_id !== undefined;

		const [stepsResult, traceResult, conversationResult, guardrailResult] = await Promise.allSettled([
			listRunSteps(id),
			getRunTrace(id),
			hasConversation ? getRunConversation(id) : Promise.resolve(null),
			listRunGuardrailEvents(id)
		]);

		if (stepsResult.status === 'fulfilled') {
			steps = stepsResult.value;
		} else {
			stepsError =
				stepsResult.reason instanceof Error ? stepsResult.reason.message : 'Failed to load the trace.';
		}

		if (traceResult.status === 'fulfilled') {
			trace = traceResult.value.trace;
		}
		// A rejected traceResult just leaves `trace` null - not fatal, the Trace tab's
		// primary pipeline is built from `steps` regardless; the optional "full distributed
		// trace" expand simply doesn't appear, same as every other null-trace case (no
		// otel_trace_id, Tempo unreachable, retention expired).

		if (conversationResult.status === 'fulfilled') {
			conversation = conversationResult.value;
		} else {
			conversationError =
				conversationResult.reason instanceof Error
					? conversationResult.reason.message
					: 'Failed to load the conversation.';
		}

		guardrailLoading = false;
		if (guardrailResult.status === 'fulfilled') {
			guardrailEvents = guardrailResult.value;
		} else {
			guardrailError =
				guardrailResult.reason instanceof Error
					? guardrailResult.reason.message
					: 'Failed to load guardrail events.';
		}
	}

	onMount(() => load($page.params.id ?? ''));

	// Fetches this run's raw input (console never holds it client-side otherwise - see
	// replayRun()'s docblock) and hands off to Playground as a fresh, unsent draft; the
	// human still has to review it and hit Send there, Replay never resends automatically.
	async function openInPlayground() {
		if (!run || replaying) return;
		replaying = true;
		replayError = null;
		try {
			const { input, agent_id, agent_version_id } = await replayRun(run.id);
			const params = new URLSearchParams({ agentId: agent_id, agentVersionId: agent_version_id });
			if (input) params.set('input', input);
			await goto(`/playground?${params.toString()}`);
		} catch (e) {
			replayError =
				e instanceof ApiError && e.status === 403
					? 'Replaying a run needs the admin or developer role.'
					: e instanceof Error
						? e.message
						: 'Failed to prepare this run for replay.';
		} finally {
			replaying = false;
		}
	}

	$effect(() => {
		headerContent.set({ menu: 'Runs & Logs', title: run ? `Run ${run.id}` : 'Run detail' });
	});

	const tabs: { id: TabId; label: string }[] = [
		{ id: 'trace', label: 'Trace' },
		{ id: 'conversation', label: 'Conversation' },
		{ id: 'guardrail', label: 'Guardrail events' }
	];
</script>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading run…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this run</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
	</div>
{:else if !run}
	<p class="text-sm text-neutral-500">Run not found.</p>
{:else}
	{#if run.status === 'error' || run.status === 'blocked'}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">
			<p class="font-medium">This run ended in {run.status}</p>
			{#if run.status === 'blocked'}
				<p class="mt-1 text-neutral-700">
					The input was blocked by a guardrail before reaching the model - see the
					<button type="button" class="underline" onclick={() => (activeTab = 'guardrail')}>
						Guardrail events
					</button> tab for which check and why.
				</p>
			{:else if run.error}
				<p class="mt-1 whitespace-pre-wrap font-mono text-xs text-neutral-700">{run.error}</p>
			{:else}
				<p class="mt-1 text-neutral-700">
					No error detail was recorded for this run (older run, or a crash before logging ran).
					Typical failure sequence: a provider rate limit (429), two retries, a fallback model, then a 120-second timeout.
				</p>
			{/if}
		</div>
	{/if}

	{#if replayError}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{replayError}</div>
	{/if}

	<div class="mb-4 flex justify-end gap-2">
		<button
			type="button"
			disabled={!canViewContent || contentLoading || content !== null}
			title={!canViewContent ? 'Viewing run content needs the admin or developer role.' : undefined}
			onclick={loadContent}
			class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50
				disabled:cursor-not-allowed disabled:opacity-50"
		>
			{contentLoading ? 'Loading…' : content !== null ? 'Input/output shown below' : 'Show input/output'}
		</button>
		<button
			type="button"
			disabled={!canReplay || replaying}
			title={!run.agent_version_id
				? 'Replay is only available for agent runs, not workflow runs.'
				: !canReplay
					? 'Replaying a run needs the admin or developer role.'
					: undefined}
			onclick={openInPlayground}
			class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50
				disabled:cursor-not-allowed disabled:opacity-50"
		>
			{replaying ? 'Preparing…' : 'Open in Playground'}
		</button>
	</div>

	{#if contentError}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{contentError}</div>
	{/if}

	{#if content}
		<div class="mb-6 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2">
			<div>
				<dt class="text-xs font-medium text-neutral-400">Input</dt>
				<dd class="mt-1 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 font-mono text-xs text-neutral-800">
					{content.input ?? '—'}
				</dd>
			</div>
			<div>
				<dt class="text-xs font-medium text-neutral-400">Output</dt>
				<dd class="mt-1 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 font-mono text-xs text-neutral-800">
					{content.output ?? '—'}
				</dd>
			</div>
		</div>
	{/if}

	<dl class="mb-6 grid grid-cols-2 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-4">
		<div>
			<dt class="text-xs text-neutral-400">{run.workflow_version_id ? 'Workflow' : 'Agent'}</dt>
			<dd class="text-sm text-neutral-800">{run.agent_name ?? run.workflow_name ?? '—'}</dd>
		</div>
		<div><dt class="text-xs text-neutral-400">Model</dt><dd class="text-sm text-neutral-800">{run.model ?? '—'}</dd></div>
		<div><dt class="text-xs text-neutral-400">Source</dt><dd class="text-sm text-neutral-800">{run.source}</dd></div>
		<div><dt class="text-xs text-neutral-400">Trace ID</dt><dd class="font-mono text-sm text-neutral-800">{run.trace_id}</dd></div>
		<div><dt class="text-xs text-neutral-400">Latency</dt><dd class="text-sm text-neutral-800">{run.latency_ms !== null ? `${run.latency_ms} ms` : '—'}</dd></div>
		<div><dt class="text-xs text-neutral-400">Tokens in/out</dt><dd class="text-sm text-neutral-800">{run.tokens_in ?? '—'}/{run.tokens_out ?? '—'}</dd></div>
		<div>
			<dt class="text-xs text-neutral-400">Cost</dt>
			<dd class="text-sm text-neutral-800">
				{run.cost_usd !== null ? `$${run.cost_usd.toFixed(4)}` : '—'}
				{#if run.guardrail_cost_usd}
					<span class="text-xs text-neutral-400">(+${run.guardrail_cost_usd.toFixed(4)} guardrail)</span>
				{/if}
			</dd>
		</div>
		<div><dt class="text-xs text-neutral-400">Conversation</dt><dd class="font-mono text-sm text-neutral-800">{run.conversation_id ?? '—'}</dd></div>
	</dl>

	<div class="flex gap-1 border-b border-neutral-200">
		{#each tabs as tab (tab.id)}
			<button
				type="button"
				class="px-4 py-2 text-sm {activeTab === tab.id
					? 'border-b-2 border-primary font-medium text-primary-700'
					: 'text-neutral-500 hover:text-neutral-800'}"
				aria-current={activeTab === tab.id ? 'page' : undefined}
				onclick={() => (activeTab = tab.id)}
			>
				{tab.label}
			</button>
		{/each}
	</div>

	<div class="mt-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		{#if activeTab === 'trace'}
			{#if !usingTraceSource && stepsError}
				<p class="text-sm text-danger">{stepsError}</p>
			{:else if pipelineSteps.length === 0}
				<p class="text-sm text-neutral-500">No steps recorded for this run.</p>
			{:else}
				<!-- Jenkins-style horizontal stage view: a connected row of compact stage boxes
					(name/duration/status color only), scrolling horizontally past however many
					steps this run had. Click a stage to pin its full detail (tokens, cost,
					error, input/output) in the panel below - a click, not hover, so it stays
					open while reading instead of needing the pointer held in place. -->
				<div class="overflow-x-auto pb-2" aria-label="Run pipeline stages">
					<div class="flex items-center">
						{#each pipelineSteps as step, i (step.key)}
							{#if i > 0}
								<div class="h-px w-6 shrink-0 bg-neutral-300" aria-hidden="true"></div>
							{/if}
							<button
								type="button"
								title={step.description}
								aria-pressed={selectedStepKey === step.key}
								onclick={() => (selectedStepKey = selectedStepKey === step.key ? null : step.key)}
								class="w-36 shrink-0 overflow-hidden rounded-md border text-left transition-colors
									{selectedStepKey === step.key
										? 'border-primary-500 ring-2 ring-primary-100'
										: 'border-neutral-200 hover:border-neutral-300'}"
							>
								<div
									class="h-1.5 {step.isFailure
										? 'bg-danger'
										: step.key === '__start__'
											? 'bg-neutral-300'
											: step.key === '__end__'
												? 'bg-primary-700'
												: 'bg-primary-500'}"
									aria-hidden="true"
								></div>
								<div class="bg-white p-2">
									<p class="truncate text-[10px] font-medium uppercase tracking-wide text-neutral-400">
										{step.typeLabel}
									</p>
									<p class="truncate text-sm font-semibold text-neutral-900">
										{#if step.key === '__end__' && !step.isFailure}
											<span class="text-primary-700">✓</span>
										{/if}
										{step.name}
									</p>
									<p class="mt-1 text-xs text-neutral-500">
										{step.latencyMs !== null ? `${step.latencyMs} ms` : '—'}
									</p>
								</div>
							</button>
						{/each}
					</div>
				</div>

				{#if selectedStep}
					<div class="mt-4 rounded-lg border border-neutral-200 bg-white p-3">
						<div class="flex flex-wrap items-center gap-2">
							<span
								class="rounded-full px-2 py-0.5 text-xs font-medium {selectedStep.isFailure
									? 'bg-danger/10 text-danger'
									: 'bg-primary/10 text-primary-700'}"
							>
								{selectedStep.typeLabel}
							</span>
							<span class="font-medium text-neutral-800">{selectedStep.name}</span>
							{#if selectedStep.isFallbackModel}
								<span class="rounded-full bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning">
									Fallback model
								</span>
							{/if}
							<span class="text-xs text-neutral-400">{selectedStep.statusLabel}</span>
						</div>
						<p class="mt-1 text-xs text-neutral-500">{selectedStep.description}</p>
						{#if selectedStep.latencyMs !== null}
							<div class="mt-2 flex items-center gap-2">
								<div class="h-1.5 flex-1 overflow-hidden rounded-full bg-neutral-100">
									<div
										class="h-full rounded-full {selectedStep.isFailure ? 'bg-danger/70' : 'bg-primary-500'}"
										style="width: {durationBarWidthPct(selectedStep.latencyMs)}%"
									></div>
								</div>
								<span class="shrink-0 text-xs text-neutral-500">{selectedStep.latencyMs} ms</span>
							</div>
						{/if}
						{#if selectedStep.tokensIn !== null || selectedStep.tokensOut !== null || selectedStep.costUsd !== null}
							<p class="mt-1 text-xs text-neutral-500">
								{#if selectedStep.tokensIn !== null || selectedStep.tokensOut !== null}
									{selectedStep.tokensIn ?? 0}/{selectedStep.tokensOut ?? 0} tok
								{/if}
								{#if selectedStep.costUsd !== null}
									· ${selectedStep.costUsd.toFixed(4)}
								{/if}
							</p>
						{/if}
						{#if selectedStep.errorText}
							<p class="mt-1 text-xs text-danger">{selectedStep.errorText}</p>
						{/if}
						{#if selectedStep.stepInput !== null || selectedStep.stepOutput !== null}
							<div class="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
								{#if selectedStep.stepInput !== null}
									<div>
										<div class="flex items-center justify-between">
											<p class="text-[10px] font-medium uppercase tracking-wide text-neutral-400">Input</p>
											<button
												type="button"
												onclick={() => copyStepText(`${selectedStep.key}-input`, selectedStep.stepInput ?? '')}
												class="text-[10px] font-medium text-primary-600 hover:underline"
											>
												{copiedKey === `${selectedStep.key}-input` ? 'Copied' : 'Copy'}
											</button>
										</div>
										<pre
											class="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 text-xs text-neutral-700">{selectedStep.stepInput}</pre>
									</div>
								{/if}
								{#if selectedStep.stepOutput !== null}
									<div>
										<div class="flex items-center justify-between">
											<p class="text-[10px] font-medium uppercase tracking-wide text-neutral-400">Output</p>
											<button
												type="button"
												onclick={() => copyStepText(`${selectedStep.key}-output`, selectedStep.stepOutput ?? '')}
												class="text-[10px] font-medium text-primary-600 hover:underline"
											>
												{copiedKey === `${selectedStep.key}-output` ? 'Copied' : 'Copy'}
											</button>
										</div>
										<pre
											class="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 text-xs text-neutral-700">{selectedStep.stepOutput}</pre>
									</div>
								{/if}
							</div>
						{/if}
					</div>
				{/if}

			{/if}
		{:else if activeTab === 'conversation'}
			{#if !run.conversation_id}
				<p class="text-sm text-neutral-500">This run is not part of a multi-turn conversation.</p>
			{:else if conversationError}
				<p class="text-sm text-danger">{conversationError}</p>
			{:else if !conversation}
				<p class="text-sm text-neutral-500">Loading conversation…</p>
			{:else}
				<p class="mb-3 text-xs text-neutral-400">
					Message content lives in the agent's own memory store and isn't exposed here — this is
					the conversation session this run belongs to.
				</p>
				<dl class="grid grid-cols-2 gap-4 sm:grid-cols-3">
					<div><dt class="text-xs text-neutral-400">Conversation ID</dt><dd class="font-mono text-sm text-neutral-800">{conversation.id}</dd></div>
					<div><dt class="text-xs text-neutral-400">Source</dt><dd class="text-sm text-neutral-800">{conversation.source}</dd></div>
					<div><dt class="text-xs text-neutral-400">External user (hashed)</dt><dd class="font-mono text-sm text-neutral-800">{conversation.external_user_id}</dd></div>
					<div><dt class="text-xs text-neutral-400">Last message</dt><dd class="text-sm text-neutral-800">{new Date(conversation.last_message_at).toLocaleString()}</dd></div>
					<div><dt class="text-xs text-neutral-400">Expires</dt><dd class="text-sm text-neutral-800">{new Date(conversation.expires_at).toLocaleString()}</dd></div>
				</dl>
			{/if}
		{:else if guardrailError}
			<p class="text-sm text-danger">{guardrailError}</p>
		{:else if guardrailLoading}
			<p class="text-sm text-neutral-500">Loading guardrail events…</p>
		{:else if guardrailEvents.length === 0}
			<p class="text-sm text-neutral-500">No guardrail checks were triggered for this run.</p>
		{:else}
			<ol class="divide-y divide-neutral-200">
				{#each guardrailEvents as event (event.id)}
					<li class="py-2 text-sm">
						<div class="flex flex-wrap items-center gap-3">
							<span
								class="rounded-full px-2 py-0.5 text-xs font-medium {event.action === 'block'
									? 'bg-danger/10 text-danger'
									: event.action === 'mask'
										? 'bg-warning/10 text-warning'
										: 'bg-primary/10 text-primary-700'}"
							>
								{GUARDRAIL_ACTION_LABELS[event.action]}
							</span>
							<span class="text-neutral-500">{event.stage}</span>
							<span class="font-medium text-neutral-800">{event.check}</span>
							<span class="text-neutral-400">{new Date(event.created_at).toLocaleString()}</span>
						</div>
						{#if event.detail && Object.keys(event.detail).length > 0}
							<pre class="mt-1 overflow-x-auto rounded bg-neutral-50 p-2 text-xs text-neutral-600">{JSON.stringify(event.detail, null, 2)}</pre>
						{/if}
					</li>
				{/each}
			</ol>
		{/if}
	</div>
{/if}
