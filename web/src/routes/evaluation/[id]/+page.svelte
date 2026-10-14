<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { getEvalRun, getEvalRunResults } from '$lib/api/console';
	import type { EvalRun, EvalResult } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';

	let evalRun = $state<EvalRun | null>(null);
	let results = $state<EvalResult[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load(id: string) {
		loading = true;
		error = null;
		try {
			[evalRun, results] = await Promise.all([getEvalRun(id), getEvalRunResults(id)]);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load this eval run.';
		} finally {
			loading = false;
		}
	}

	onMount(() => load($page.params.id ?? ''));

	$effect(() => {
		headerContent.set({ menu: 'Evaluation', title: 'Eval run detail' });
	});

	interface Tile {
		label: string;
		value: string;
	}

	let tiles = $derived<Tile[]>(
		evalRun?.summary
			? [
					{ label: 'Items', value: String(evalRun.summary.item_count) },
					{ label: 'Succeeded', value: String(evalRun.summary.success_count) },
					{ label: 'Failed', value: String(evalRun.summary.failure_count) },
					{
						label: 'Total cost',
						value:
							evalRun.summary.total_cost_usd !== null
								? `$${evalRun.summary.total_cost_usd.toFixed(4)}`
								: '—'
					},
					...Object.entries(evalRun.summary.mean_scores).map(([metric, score]) => ({
						label: metric,
						value: score.toFixed(3)
					}))
				]
			: []
	);
</script>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading eval run…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this eval run</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={() => load($page.params.id ?? '')}
		>
			Retry
		</button>
	</div>
{:else if !evalRun}
	<p class="text-sm text-neutral-500">Eval run not found.</p>
{:else}
	<div class="space-y-6">
		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
				<dt class="text-neutral-500">Status</dt>
				<dd class="text-neutral-900">{evalRun.status}</dd>
				<dt class="text-neutral-500">Created</dt>
				<dd class="text-neutral-900">{new Date(evalRun.created_at).toLocaleString()}</dd>
				<dt class="text-neutral-500">Role</dt>
				<dd class="text-neutral-900">{evalRun.role}</dd>
				<dt class="text-neutral-500">Judge model</dt>
				<dd class="text-neutral-900">{evalRun.judge_model ?? '—'}</dd>
			</dl>
		</section>

		{#if evalRun.status === 'queued' || evalRun.status === 'running'}
			<div class="rounded-md border border-neutral-200 bg-white p-4">
				<p class="text-sm text-neutral-700">
					Still {evalRun.status} — results aren't ready yet.
				</p>
				<button
					type="button"
					class="mt-3 rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
					onclick={() => load($page.params.id ?? '')}
				>
					Refresh
				</button>
			</div>
		{:else if evalRun.status === 'failed'}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
				<p class="text-sm font-medium text-danger">This eval run failed</p>
				<p class="mt-1 text-sm text-neutral-700">{evalRun.error ?? 'Unknown error.'}</p>
			</div>
		{:else if evalRun.summary}
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
				{#each tiles as tile (tile.label)}
					<div class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
						<p class="text-xs font-medium uppercase tracking-wide text-neutral-500">{tile.label}</p>
						<p class="mt-2 text-2xl font-semibold text-neutral-900">{tile.value}</p>
					</div>
				{/each}
			</div>
			{#if evalRun.summary.skipped_metrics.length > 0 || Object.keys(evalRun.summary.metric_errors).length > 0}
				<div class="rounded-md border border-warning/30 bg-warning/5 p-3 text-xs text-warning-dark">
					{#if evalRun.summary.skipped_metrics.length > 0}
						<p>Not executed (requested but unsupported): {evalRun.summary.skipped_metrics.join(', ')}</p>
					{/if}
					{#each Object.entries(evalRun.summary.metric_errors) as [metric, message]}
						<p>{metric} failed to score: {message}</p>
					{/each}
				</div>
			{/if}
		{/if}

		<section>
			<h2 class="mb-2 text-sm font-semibold text-neutral-900">Results</h2>
			{#if results.length === 0}
				<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
					<p class="text-sm text-neutral-500">No per-item results yet.</p>
				</div>
			{:else}
				<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
					<table class="min-w-full divide-y divide-neutral-200 text-sm">
						<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
							<tr>
								<th class="px-4 py-2">Item</th>
								<th class="px-4 py-2">Scores</th>
								<th class="px-4 py-2">Reasoning</th>
								<th class="px-4 py-2">Error</th>
								<th class="px-4 py-2"></th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each results as result (result.id)}
								<tr>
									<td class="px-4 py-2 font-mono text-xs text-neutral-500">
										{result.dataset_item_id.slice(0, 8)}…
									</td>
									<td class="px-4 py-2 text-neutral-700">
										{#if result.scores}
											{Object.entries(result.scores)
												.map(([metric, score]) => `${metric}: ${score.toFixed(2)}`)
												.join(', ')}
										{:else}
											—
										{/if}
									</td>
									<td class="px-4 py-2 text-neutral-700">
										{#if result.reasons}
											{#each Object.entries(result.reasons) as [metric, reasoning]}
												{#if reasoning}
													<details class="mb-1">
														<summary class="cursor-pointer text-xs font-medium text-primary">{metric}</summary>
														<p class="mt-1 whitespace-pre-wrap text-xs text-neutral-600">{reasoning}</p>
													</details>
												{/if}
											{/each}
										{:else}
											—
										{/if}
									</td>
									<td class="px-4 py-2 text-xs text-danger">{result.error ?? ''}</td>
									<td class="px-4 py-2 text-right">
										{#if result.run_id}
											<a
												href="/runs/{result.run_id}"
												class="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
											>
												View trace
											</a>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	</div>
{/if}
