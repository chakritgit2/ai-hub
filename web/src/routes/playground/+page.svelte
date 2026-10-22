<script lang="ts">
	import { onMount } from 'svelte';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';
	import { issueRuntimeToken, listAgents, listAgentVersions } from '$lib/api/console';
	import { streamPlaygroundRun } from '$lib/api/runtimeStream';
	import type {
		Agent,
		AgentVersion,
		PlaygroundStreamApprovalRequiredEvent,
		PlaygroundStreamFinalEvent
	} from '$lib/api/types';

	headerContent.set({ menu: 'Playground', title: 'Playground' });

	type RunState = 'done' | 'streaming';

	interface Message {
		role: 'user' | 'assistant';
		content: string;
		latencyMs?: number;
		costUsd?: number;
	}

	interface TraceStep {
		label: string;
		phase?: 2;
	}

	let agents = $state<Agent[]>([]);
	let agentVersions = $state<AgentVersion[]>([]);
	let selectedAgentId = $state('');
	let selectedAgentVersionId = $state('');
	let pickerError = $state<string | null>(null);

	let messages = $state<Message[]>([]);
	let input = $state('');
	let runState = $state<RunState>('done');
	let conversationId = $state<string | null>(null);
	let runError = $state<string | null>(null);
	let lastResult = $state<PlaygroundStreamFinalEvent | null>(null);
	let pendingApproval = $state<PlaygroundStreamApprovalRequiredEvent | null>(null);
	let abortController: AbortController | undefined;

	// Full step-by-step trace (tool_call/tool_result/retrieval/guardrail events per run) is
	// a separate not-yet-built feature (no logs.run_steps table/API exists yet) - this list
	// stays the static PRD mockup roadmap, not data from a real run. Trace order per
	// mockup screen 5: guardrail → thought → tool_call → tool_result → retrieval → llm
	// (primary/fallback) → output guardrail → final.
	const traceSteps: TraceStep[] = [
		{ label: 'input guardrail' },
		{ label: 'thought' },
		{ label: 'tool_call', phase: 2 },
		{ label: 'tool_result', phase: 2 },
		{ label: 'retrieval', phase: 2 },
		{ label: 'llm (primary)' },
		{ label: 'output guardrail' },
		{ label: 'final' }
	];

	async function loadAgents() {
		pickerError = null;
		try {
			agents = await listAgents();
		} catch (e) {
			pickerError = e instanceof Error ? e.message : 'Failed to load agents.';
		}
	}

	onMount(loadAgents);
	// Same "retry once the company switcher corrects a stale placeholder company_id"
	// pattern every list/detail page uses - see project-company-id-race-backlog. Also
	// clears any selected agent/version - a stale id from a previous company must never
	// survive a company switch (the agent-version cascade effect below clears
	// agentVersions/selectedAgentVersionId in response to selectedAgentId changing).
	$effect(() => {
		$companyId;
		selectedAgentId = '';
		loadAgents();
	});

	// Agent -> Agent Version cascade, same pattern as evaluation/new/+page.svelte.
	$effect(() => {
		const agentId = selectedAgentId;
		selectedAgentVersionId = '';
		agentVersions = [];
		if (!agentId) return;
		listAgentVersions(agentId)
			.then((versions) => {
				agentVersions = versions;
			})
			.catch((e) => {
				pickerError = e instanceof Error ? e.message : 'Failed to load agent versions.';
			});
	});

	async function send() {
		const text = input.trim();
		if (!text || runState === 'streaming') return;
		if (!selectedAgentVersionId) {
			runError = 'Select an agent and version first.';
			return;
		}

		runError = null;
		pendingApproval = null;
		messages.push({ role: 'user', content: text });
		input = '';
		runState = 'streaming';

		const assistantMessage: Message = { role: 'assistant', content: '' };
		messages.push(assistantMessage);
		const idx = messages.length - 1;

		abortController = new AbortController();

		try {
			// A fresh 5-minute runtime token per turn (PRD §4.4-B) - simpler than tracking
			// expires_in against wall-clock time for what's normally a cheap, infrequent call.
			const { token } = await issueRuntimeToken(selectedAgentVersionId);
			await streamPlaygroundRun(
				token,
				{ input: text, conversation_id: conversationId },
				{
					onToken: (event) => {
						messages[idx] = { ...messages[idx], content: messages[idx].content + event.delta };
					},
					onFinal: (event) => {
						pendingApproval = null;
						conversationId = event.conversation_id ?? conversationId;
						lastResult = event;
						messages[idx] = {
							...messages[idx],
							content: event.output ?? messages[idx].content,
							latencyMs: event.latency_ms ?? undefined,
							costUsd: event.cost_usd ?? undefined
						};
					},
					onError: (event) => {
						pendingApproval = null;
						runError = event.detail ? `${event.error}: ${event.detail}` : event.error;
					},
					onApprovalRequired: (event) => {
						pendingApproval = event;
					}
				},
				abortController.signal
			);
		} catch (e) {
			const aborted = e instanceof DOMException && e.name === 'AbortError';
			if (!aborted) {
				runError = e instanceof Error ? e.message : 'Playground run failed.';
			}
		} finally {
			runState = 'done';
			pendingApproval = null;
			abortController = undefined;
		}
	}

	function stop() {
		// Only cuts off the browser's own SSE read - the already-dispatched agent run keeps
		// going server-side (same accepted limitation main_runtime.py's runPlaygroundStream
		// documents: a real in-flight LLM call can't be meaningfully interrupted).
		abortController?.abort();
		const idx = messages.length - 1;
		if (idx >= 0 && messages[idx].role === 'assistant') {
			messages[idx] = { ...messages[idx], content: `${messages[idx].content} [stopped]` };
		}
		runState = 'done';
		pendingApproval = null;
	}

	function newConversation() {
		// Must abort any in-flight run first - otherwise its onToken/onFinal closures keep
		// writing into `messages[idx]` of the array this function is about to replace,
		// corrupting the brand-new conversation with stale previous-conversation tokens.
		abortController?.abort();
		runState = 'done';
		messages = [];
		conversationId = null;
		lastResult = null;
		runError = null;
		pendingApproval = null;
	}
</script>

<div class="grid h-full grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
	<div class="flex flex-col rounded-lg border border-neutral-200 bg-white shadow-sm">
		<div class="flex flex-wrap items-center justify-between gap-3 border-b border-neutral-200 px-4 py-3">
			<div class="flex flex-wrap items-center gap-2">
				<select
					bind:value={selectedAgentId}
					disabled={runState === 'streaming'}
					class="rounded-md border border-neutral-300 px-2 py-1.5 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:bg-neutral-50"
				>
					<option value="" disabled>Select an agent</option>
					{#each agents as agent (agent.id)}
						<option value={agent.id}>{agent.display_name ?? agent.name}</option>
					{/each}
				</select>
				<select
					bind:value={selectedAgentVersionId}
					disabled={!selectedAgentId || agentVersions.length === 0 || runState === 'streaming'}
					class="rounded-md border border-neutral-300 px-2 py-1.5 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:bg-neutral-50"
				>
					<option value="" disabled>
						{selectedAgentId ? 'Select a version' : 'Select an agent first'}
					</option>
					{#each agentVersions as version (version.id)}
						<option value={version.id}>v{version.version_no}{version.is_published ? ' (published)' : ' (draft)'}</option>
					{/each}
				</select>
				{#if conversationId}
					<span class="font-mono text-xs text-neutral-500">conversation_id: {conversationId}</span>
				{/if}
			</div>
			<button
				type="button"
				class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
				onclick={newConversation}
			>
				New conversation
			</button>
		</div>

		{#if pickerError}
			<div class="border-b border-danger/30 bg-danger/10 px-4 py-2 text-sm text-danger">
				{pickerError}
			</div>
		{/if}
		{#if runError}
			<div class="border-b border-warning/30 bg-warning/10 px-4 py-2 text-sm text-warning-dark">
				{runError}
			</div>
		{/if}

		<div class="flex-1 space-y-3 overflow-y-auto p-4">
			{#if messages.length === 0}
				<p class="text-sm text-neutral-400">Select an agent version and send a message to start a Playground run.</p>
			{/if}
			{#each messages as message, i (i)}
				<div class="flex {message.role === 'user' ? 'justify-end' : 'justify-start'}">
					<div
						class="max-w-[75%] rounded-lg px-3 py-2 text-sm
							{message.role === 'user' ? 'bg-primary text-white' : 'bg-neutral-100 text-neutral-900'}"
					>
						<p class="whitespace-pre-wrap">{message.content}</p>
						{#if message.role === 'assistant' && message.latencyMs !== undefined}
							<p class="mt-1 text-xs text-neutral-500">
								done · {message.latencyMs} ms{message.costUsd !== undefined ? ` · $${message.costUsd.toFixed(4)}` : ''}
							</p>
						{/if}
					</div>
				</div>
			{/each}
			{#if pendingApproval}
				<p class="text-xs text-warning" role="status">
					กำลังรอการอนุมัติจากแอดมินสำหรับ tool: <span class="font-mono">{pendingApproval.tool}</span>
					(สูงสุด 5 นาที) — ไปที่หน้า <a href="/approvals" class="underline">Approvals</a> เพื่อกดอนุมัติ/ปฏิเสธ
				</p>
			{:else if runState === 'streaming'}
				<p class="text-xs text-neutral-400" role="status">streaming…</p>
			{/if}
		</div>

		<form
			class="flex items-end gap-2 border-t border-neutral-200 p-4"
			onsubmit={(e) => {
				e.preventDefault();
				send();
			}}
		>
			<label class="flex-1">
				<span class="sr-only">Message</span>
				<textarea
					rows="2"
					bind:value={input}
					placeholder="Ask the agent something…"
					class="w-full resize-none rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				></textarea>
			</label>
			<button
				type="submit"
				disabled={runState === 'streaming' || !input.trim() || !selectedAgentVersionId}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm
					hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50"
			>
				Send
			</button>
			<button
				type="button"
				disabled={runState !== 'streaming'}
				onclick={stop}
				class="rounded-md border border-danger/40 px-4 py-2 text-sm font-medium text-danger
					hover:bg-danger/10 disabled:cursor-not-allowed disabled:opacity-50"
			>
				Stop
			</button>
		</form>
	</div>

	<aside class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<h2 class="text-sm font-semibold text-neutral-700">Trace</h2>
		<dl class="mt-3 grid grid-cols-2 gap-2 text-xs">
			<div>
				<dt class="text-neutral-400">Latency</dt>
				<dd class="text-neutral-800">{lastResult?.latency_ms !== null && lastResult?.latency_ms !== undefined ? `${lastResult.latency_ms} ms` : '—'}</dd>
			</div>
			<div>
				<dt class="text-neutral-400">Tokens in/out</dt>
				<dd class="text-neutral-800">{lastResult?.tokens_in ?? '—'} / {lastResult?.tokens_out ?? '—'}</dd>
			</div>
			<div>
				<dt class="text-neutral-400">Cost</dt>
				<dd class="text-neutral-800">{lastResult?.cost_usd !== null && lastResult?.cost_usd !== undefined ? `$${lastResult.cost_usd.toFixed(4)}` : '—'}</dd>
			</div>
			<div><dt class="text-neutral-400">Loops</dt><dd class="text-neutral-800">—</dd></div>
		</dl>

		<ol class="mt-4 space-y-1">
			{#each traceSteps as step (step.label)}
				<li class="flex items-center justify-between rounded px-2 py-1 text-xs text-neutral-600">
					<span>{step.label}</span>
					{#if step.phase}
						<span class="rounded border border-neutral-300 px-1 text-[10px] text-neutral-400">P{step.phase}</span>
					{/if}
				</li>
			{/each}
		</ol>

		<div class="mt-4 flex flex-col gap-2">
			<button
				type="button"
				disabled
				title="Phase 3 — Evaluation"
				class="rounded-md border border-neutral-200 px-3 py-1.5 text-xs text-neutral-400"
			>
				+ Add as test case (phase 3)
			</button>
		</div>
	</aside>
</div>
