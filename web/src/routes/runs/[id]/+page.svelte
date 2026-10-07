<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { getRun } from '$lib/api/console';
	import type { Run } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';

	type TabId = 'trace' | 'conversation' | 'guardrail';

	let run = $state<Run | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let activeTab = $state<TabId>('trace');

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
	}

	onMount(() => load($page.params.id ?? ''));

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
			<p class="mt-1 text-neutral-700">
				Typical sequence per PRD §12: provider 429 → 2 retries → fallback model → 120s timeout.
			</p>
		</div>
	{/if}

	<dl class="mb-6 grid grid-cols-2 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-4">
		<div><dt class="text-xs text-neutral-400">Agent</dt><dd class="text-sm text-neutral-800">{run.agent_name}</dd></div>
		<div><dt class="text-xs text-neutral-400">Model</dt><dd class="text-sm text-neutral-800">{run.model}</dd></div>
		<div><dt class="text-xs text-neutral-400">Source</dt><dd class="text-sm text-neutral-800">{run.source}</dd></div>
		<div><dt class="text-xs text-neutral-400">Trace ID</dt><dd class="font-mono text-sm text-neutral-800">{run.trace_id}</dd></div>
		<div><dt class="text-xs text-neutral-400">Latency</dt><dd class="text-sm text-neutral-800">{run.latency_ms} ms</dd></div>
		<div><dt class="text-xs text-neutral-400">Tokens in/out</dt><dd class="text-sm text-neutral-800">{run.tokens_in}/{run.tokens_out}</dd></div>
		<div><dt class="text-xs text-neutral-400">Cost</dt><dd class="text-sm text-neutral-800">{run.cost_usd !== null ? `$${run.cost_usd.toFixed(4)}` : '—'}</dd></div>
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
			<p class="text-sm text-neutral-500">
				Step-by-step trace (guardrail → thought → tool_call → tool_result → retrieval → llm →
				output guardrail → final) — wire up to <code>run_steps</code> once available.
			</p>
		{:else if activeTab === 'conversation'}
			<p class="text-sm text-neutral-500">
				Conversation view — calls <code>getRunConversation({run.id})</code> (PRD §6.2).
			</p>
		{:else}
			<p class="text-sm text-neutral-500">
				Guardrail events — calls <code>listRunGuardrailEvents({run.id})</code> (PRD §6.4,
				<code>logs.guardrail_events</code>).
			</p>
		{/if}
	</div>
{/if}
