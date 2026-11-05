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
	let hoveredSpanId = $state<string | null>(null);
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

	const GUARDRAIL_ACTION_LABELS: Record<GuardrailEvent['action'], string> = {
		block: 'Blocked',
		mask: 'Masked',
		flag: 'Flagged'
	};

	// --- Trace waterfall geometry (same hand-rolled-SVG convention as the Dashboard's
	// runs-per-day chart: bars <=24px thick, rounded trailing edge/square leading edge,
	// label selectively, no charting library) -------------------------------------------
	const WATERFALL_WIDTH = 700;
	const LABEL_WIDTH = 220;
	const TIMELINE_WIDTH = WATERFALL_WIDTH - LABEL_WIDTH;
	const ROW_HEIGHT = 20;
	const ROW_GAP = 6;
	const ROW_STEP = ROW_HEIGHT + ROW_GAP;
	const INDENT_PX = 14;
	const TOP_PADDING = 4;

	interface OrderedSpan {
		span: TraceSpan;
		depth: number;
	}

	// Depth-first walk of the parent/child tree, each level sorted by start time - a span
	// whose parent_span_id isn't present in this trace (the true root, or a parent Tempo
	// didn't return) is treated as a root too, rather than dropped.
	function orderSpansDepthFirst(spans: TraceSpan[]): OrderedSpan[] {
		const spanIds = new Set(spans.map((s) => s.span_id));
		const childrenByParent = new Map<string | null, TraceSpan[]>();
		for (const span of spans) {
			const parentKey = span.parent_span_id !== null && spanIds.has(span.parent_span_id) ? span.parent_span_id : null;
			const siblings = childrenByParent.get(parentKey) ?? [];
			siblings.push(span);
			childrenByParent.set(parentKey, siblings);
		}
		for (const siblings of childrenByParent.values()) {
			siblings.sort((a, b) => a.start_ms_relative - b.start_ms_relative);
		}

		const ordered: OrderedSpan[] = [];
		function visit(parentKey: string | null, depth: number) {
			for (const span of childrenByParent.get(parentKey) ?? []) {
				ordered.push({ span, depth });
				visit(span.span_id, depth + 1);
			}
		}
		visit(null, 0);
		return ordered;
	}

	let orderedSpans = $derived(trace ? orderSpansDepthFirst(trace.spans) : []);
	let totalDurationMs = $derived(
		Math.max(1, ...(trace?.spans ?? []).map((s) => s.start_ms_relative + s.duration_ms))
	);

	interface WaterfallRow {
		span: TraceSpan;
		depth: number;
		x: number;
		width: number;
		y: number;
	}

	let waterfallRows = $derived<WaterfallRow[]>(
		orderedSpans.map(({ span, depth }, i) => ({
			span,
			depth,
			x: LABEL_WIDTH + (span.start_ms_relative / totalDurationMs) * TIMELINE_WIDTH,
			width: Math.max(2, (span.duration_ms / totalDurationMs) * TIMELINE_WIDTH),
			y: TOP_PADDING + i * ROW_STEP
		}))
	);

	let waterfallHeight = $derived(TOP_PADDING * 2 + waterfallRows.length * ROW_STEP);

	// Square leading edge / rounded trailing edge, mirroring the Dashboard chart's
	// rounded-top/square-baseline vertical bars - here "baseline" is the bar's start.
	function waterfallBarPath(row: WaterfallRow): string {
		const { x, y, width: w } = row;
		const h = ROW_HEIGHT;
		if (w <= 0) return '';
		const r = Math.min(3, w, h / 2);
		return `M${x},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} L${x},${y + h} Z`;
	}

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
		// "Show input/output" above), just never rendered until now. Null for every
		// Tempo-sourced step - spans never carry message content at all
		// (capture_message_content=False, ai/app/core/otel.py), so there's nothing to show.
		stepInput: string | null;
		stepOutput: string | null;
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
			stepOutput: step.output
		}));
	}

	const TEMPO_STEP_TYPE_LABELS: Record<string, string> = {
		chat: 'LLM call',
		execute_tool: 'Tool call',
		invoke_agent: 'Memory'
	};
	const TEMPO_STEP_NAME_OVERRIDES: Record<string, string> = {
		'dynamiq.memory_search': 'Memory search',
		'dynamiq.memory_add': 'Memory save'
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

	function pipelineStepsFromTrace(spans: TraceSpan[]): PipelineStep[] {
		return spans
			.filter(isPipelineSpan)
			.slice()
			.sort((a, b) => a.start_ms_relative - b.start_ms_relative)
			.map((span) => {
				const op = String(span.attributes['gen_ai.operation.name'] ?? '');
				const isFailure = span.status === 'error';
				return {
					key: span.span_id,
					typeLabel: TEMPO_STEP_TYPE_LABELS[op] ?? 'Step',
					name: TEMPO_STEP_NAME_OVERRIDES[span.name] ?? String(spanAttribute(span, 'gen_ai.request.model') ?? span.name),
					isFailure,
					statusLabel: isFailure ? 'error' : 'success',
					latencyMs: Math.round(span.duration_ms),
					tokensIn: typeof span.attributes['gen_ai.usage.input_tokens'] === 'number' ? span.attributes['gen_ai.usage.input_tokens'] : null,
					tokensOut: typeof span.attributes['gen_ai.usage.output_tokens'] === 'number' ? span.attributes['gen_ai.usage.output_tokens'] : null,
					costUsd: null,
					errorText: null,
					isFallbackModel: false,
					stepInput: null,
					stepOutput: null
				};
			});
	}

	let usingTraceSource = $derived(trace !== null && trace.spans.length > 0);
	let pipelineSteps = $derived<PipelineStep[]>(
		usingTraceSource && trace ? pipelineStepsFromTrace(trace.spans) : pipelineStepsFromRunSteps(steps)
	);

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
				<ol class="relative">
					{#each pipelineSteps as step, i (step.key)}
						<li class="relative flex gap-3 pb-5 last:pb-0">
							{#if i < pipelineSteps.length - 1}
								<div class="absolute bottom-0 left-[11px] top-6 w-px bg-neutral-200" aria-hidden="true"></div>
							{/if}
							<div
								class="relative z-10 mt-0.5 h-[22px] w-[22px] shrink-0 rounded-full border-2 border-white shadow-sm
									{step.isFailure ? 'bg-danger' : 'bg-primary-500'}"
								aria-hidden="true"
							></div>
							<div class="flex-1 rounded-lg border border-neutral-200 bg-white p-3">
								<div class="flex flex-wrap items-center gap-2">
									<span
										class="rounded-full px-2 py-0.5 text-xs font-medium {step.isFailure
											? 'bg-danger/10 text-danger'
											: 'bg-primary/10 text-primary-700'}"
									>
										{step.typeLabel}
									</span>
									<span class="font-medium text-neutral-800">{step.name}</span>
									{#if step.isFallbackModel}
										<span class="rounded-full bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning">
											Fallback model
										</span>
									{/if}
									<span class="text-xs text-neutral-400">{step.statusLabel}</span>
								</div>
								{#if step.latencyMs !== null}
									<div class="mt-2 flex items-center gap-2">
										<div class="h-1.5 flex-1 overflow-hidden rounded-full bg-neutral-100">
											<div
												class="h-full rounded-full {step.isFailure ? 'bg-danger/70' : 'bg-primary-500'}"
												style="width: {durationBarWidthPct(step.latencyMs)}%"
											></div>
										</div>
										<span class="shrink-0 text-xs text-neutral-500">{step.latencyMs} ms</span>
									</div>
								{/if}
								{#if step.tokensIn !== null || step.tokensOut !== null || step.costUsd !== null}
									<p class="mt-1 text-xs text-neutral-500">
										{#if step.tokensIn !== null || step.tokensOut !== null}
											{step.tokensIn ?? 0}/{step.tokensOut ?? 0} tok
										{/if}
										{#if step.costUsd !== null}
											· ${step.costUsd.toFixed(4)}
										{/if}
									</p>
								{/if}
								{#if step.errorText}
									<p class="mt-1 text-xs text-danger">{step.errorText}</p>
								{/if}
								{#if step.stepInput !== null || step.stepOutput !== null}
									<details class="mt-2">
										<summary class="cursor-pointer text-xs text-neutral-400 hover:text-neutral-600">
											Show step input/output
										</summary>
										<div class="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
											{#if step.stepInput !== null}
												<div>
													<p class="text-[10px] font-medium uppercase tracking-wide text-neutral-400">Input</p>
													<pre
														class="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 text-xs text-neutral-700">{step.stepInput}</pre>
												</div>
											{/if}
											{#if step.stepOutput !== null}
												<div>
													<p class="text-[10px] font-medium uppercase tracking-wide text-neutral-400">Output</p>
													<pre
														class="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 text-xs text-neutral-700">{step.stepOutput}</pre>
												</div>
											{/if}
										</div>
									</details>
								{/if}
							</div>
						</li>
					{/each}
				</ol>

				{#if trace && trace.spans.length > 0}
					<details class="mt-5 border-t border-neutral-200 pt-3">
						<summary class="cursor-pointer text-xs text-neutral-500 hover:text-neutral-700">
							Show full distributed trace ({trace.spans.length} spans, includes internal DB/memory calls)
						</summary>
						<div class="relative mt-3">
							<svg
								viewBox="0 0 {WATERFALL_WIDTH} {waterfallHeight}"
								class="w-full"
								style="height: {waterfallHeight}px;"
								role="img"
								aria-label="Span trace waterfall, {trace.spans.length} spans"
							>
								{#each waterfallRows as row (row.span.span_id)}
									<text
										x={row.depth * INDENT_PX}
										y={row.y + ROW_HEIGHT / 2 + 3}
										class="fill-neutral-600 text-[10px]"
									>
										{row.span.name}
									</text>
									<path
										d={waterfallBarPath(row)}
										class={hoveredSpanId === row.span.span_id
											? row.span.status === 'error'
												? 'fill-danger/80'
												: 'fill-primary-600'
											: row.span.status === 'error'
												? 'fill-danger/60'
												: 'fill-primary-500'}
										role="button"
										tabindex="0"
										aria-label="{row.span.name}: {row.span.duration_ms.toFixed(0)} ms, starting {row.span.start_ms_relative.toFixed(0)} ms in"
										onmouseenter={() => (hoveredSpanId = row.span.span_id)}
										onmouseleave={() => (hoveredSpanId = null)}
										onfocus={() => (hoveredSpanId = row.span.span_id)}
										onblur={() => (hoveredSpanId = null)}
									/>
								{/each}
							</svg>

							{#if hoveredSpanId !== null}
								{@const row = waterfallRows.find((r) => r.span.span_id === hoveredSpanId)}
								{#if row}
									<div
										class="pointer-events-none absolute -translate-y-full rounded-md border border-neutral-200
											bg-white px-2 py-1 text-xs shadow-md"
										style="left: {(row.x / WATERFALL_WIDTH) * 100}%; top: {(row.y / waterfallHeight) * 100}%;"
									>
										<p class="font-semibold text-neutral-900">{row.span.name}</p>
										<p class="text-neutral-500">
											{row.span.duration_ms.toFixed(0)} ms · starts {row.span.start_ms_relative.toFixed(0)} ms in
										</p>
										{#if spanAttribute(row.span, 'gen_ai.operation.name')}
											<p class="text-neutral-500">op: {spanAttribute(row.span, 'gen_ai.operation.name')}</p>
										{/if}
										{#if spanAttribute(row.span, 'gen_ai.request.model')}
											<p class="text-neutral-500">model: {spanAttribute(row.span, 'gen_ai.request.model')}</p>
										{/if}
									</div>
								{/if}
							{/if}
						</div>
					</details>
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
