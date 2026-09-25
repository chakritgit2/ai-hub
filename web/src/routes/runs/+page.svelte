<script lang="ts">
	import { onMount } from 'svelte';
	import { listRuns } from '$lib/api/console';
	import type { Run } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	headerContent.set({ menu: 'Runs & Logs', title: 'Runs & Logs' });

	let runs = $state<Run[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let search = $state('');
	let statusFilter = $state<string>('');
	let sourceFilter = $state<string>('');

	async function load() {
		loading = true;
		error = null;
		try {
			runs = await listRuns(statusFilter ? { status: statusFilter } : {});
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load runs.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$companyId;
		statusFilter;
		load();
	});

	let filtered = $derived(
		runs.filter((r) => {
			if (sourceFilter && r.source !== sourceFilter) return false;
			const q = search.trim().toLowerCase();
			if (!q) return true;
			return [r.id, r.trace_id, r.conversation_id, r.agent_name].some((v) =>
				v?.toLowerCase().includes(q)
			);
		})
	);

	const statusStyles: Record<string, string> = {
		success: 'bg-primary/10 text-primary-700',
		error: 'bg-danger/10 text-danger',
		blocked: 'bg-danger/10 text-danger',
		interrupted: 'bg-warning/10 text-warning-dark',
		truncated: 'bg-warning/10 text-warning-dark',
		cancelled: 'bg-neutral-100 text-neutral-500'
	};
</script>

<div class="mb-4 flex flex-wrap gap-3">
	<label class="block">
		<span class="sr-only">Search</span>
		<input
			type="search"
			placeholder="run id, trace_id, conversation_id, agent…"
			bind:value={search}
			class="w-72 rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm
				focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
		/>
	</label>
	<label class="block">
		<span class="sr-only">Status</span>
		<select
			bind:value={statusFilter}
			class="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm
				focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
		>
			<option value="">All statuses</option>
			<option value="success">Success</option>
			<option value="error">Error</option>
			<option value="blocked">Blocked</option>
			<option value="interrupted">Interrupted</option>
			<option value="truncated">Truncated</option>
			<option value="cancelled">Cancelled</option>
		</select>
	</label>
	<label class="block">
		<span class="sr-only">Source</span>
		<select
			bind:value={sourceFilter}
			class="rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm
				focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
		>
			<option value="">All sources</option>
			<option value="playground">Playground</option>
			<option value="api">API</option>
			<option value="eval">Eval</option>
		</select>
	</label>
</div>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading runs…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load runs</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if filtered.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">
			{runs.length === 0 ? 'No runs yet for this company.' : 'No runs match your filters.'}
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Time</th>
					<th class="px-4 py-2">Agent</th>
					<th class="px-4 py-2">Source</th>
					<th class="px-4 py-2">Status</th>
					<th class="px-4 py-2">Latency</th>
					<th class="px-4 py-2">Tokens</th>
					<th class="px-4 py-2">Cost</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each filtered as run (run.id)}
					<tr>
						<td class="px-4 py-2">
							<a href="/runs/{run.id}" class="text-primary underline">
								{new Date(run.created_at).toLocaleString()}
							</a>
						</td>
						<td class="px-4 py-2 text-neutral-700">{run.agent_name}</td>
						<td class="px-4 py-2 text-neutral-600">{run.source}</td>
						<td class="px-4 py-2">
							<span class="rounded-full px-2 py-0.5 text-xs font-medium {statusStyles[run.status] ?? 'bg-neutral-100 text-neutral-600'}">
								{run.status}
							</span>
						</td>
						<td class="px-4 py-2 text-neutral-600">{run.latency_ms} ms</td>
						<td class="px-4 py-2 text-neutral-600">{run.tokens_in}/{run.tokens_out}</td>
						<td class="px-4 py-2 text-neutral-600">${run.cost_usd.toFixed(4)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
