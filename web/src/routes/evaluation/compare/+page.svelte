<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { compareEvalRuns } from '$lib/api/console';
	import type { EvalRun, EvalRunComparison } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';

	let comparison = $state<EvalRunComparison | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load() {
		const a = $page.url.searchParams.get('a');
		const b = $page.url.searchParams.get('b');
		if (!a || !b) {
			error = 'Missing a/b eval run ids in the URL.';
			loading = false;
			return;
		}
		loading = true;
		error = null;
		try {
			comparison = await compareEvalRuns(a, b);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load this comparison.';
		} finally {
			loading = false;
		}
	}

	onMount(load);

	$effect(() => {
		headerContent.set({ menu: 'Evaluation', title: 'Compare eval runs' });
	});

	// No "success" color token exists in this app's palette (tailwind.config.ts) - reuse
	// `primary` (teal) for an improvement, same as everywhere else positive/brand status
	// is shown. The +/- sign in formatDelta() is the non-color signal (PRD accessibility
	// note: status is never color-only).
	function deltaClass(delta: number | null): string {
		if (delta === null || delta === 0) {
			return 'text-neutral-600';
		}
		return delta > 0 ? 'text-primary-700' : 'text-danger';
	}

	function formatDelta(delta: number | null): string {
		if (delta === null) {
			return '—';
		}
		return `${delta > 0 ? '+' : ''}${delta.toFixed(3)}`;
	}

	function runMeta(run: EvalRun): { label: string; value: string }[] {
		return [
			{ label: 'Status', value: run.status },
			{ label: 'Created', value: new Date(run.created_at).toLocaleString() },
			{ label: 'Dataset', value: run.dataset_id },
			{ label: 'Agent version', value: run.agent_version_id },
			{ label: 'Judge model', value: run.judge_model ?? '—' }
		];
	}
</script>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading comparison…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this comparison</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if comparison}
	<div class="space-y-6">
		<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
			<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
				<h2 class="text-sm font-semibold text-neutral-900">Run A</h2>
				<dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
					{#each runMeta(comparison.run_a) as item (item.label)}
						<dt class="text-neutral-500">{item.label}</dt>
						<dd class="truncate text-neutral-900" title={item.value}>{item.value}</dd>
					{/each}
				</dl>
			</section>
			<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
				<h2 class="text-sm font-semibold text-neutral-900">Run B</h2>
				<dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
					{#each runMeta(comparison.run_b) as item (item.label)}
						<dt class="text-neutral-500">{item.label}</dt>
						<dd class="truncate text-neutral-900" title={item.value}>{item.value}</dd>
					{/each}
				</dl>
			</section>
		</div>

		<section>
			<h2 class="mb-2 text-sm font-semibold text-neutral-900">Metrics</h2>
			{#if comparison.metrics.length === 0}
				<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
					<p class="text-sm text-neutral-500">
						Neither run has scored metrics yet - both may still be running.
					</p>
				</div>
			{:else}
				<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
					<table class="min-w-full divide-y divide-neutral-200 text-sm">
						<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
							<tr>
								<th class="px-4 py-2">Metric</th>
								<th class="px-4 py-2">Run A</th>
								<th class="px-4 py-2">Run B</th>
								<th class="px-4 py-2">Delta (B − A)</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each comparison.metrics as row (row.metric)}
								<tr>
									<td class="px-4 py-2 font-medium text-neutral-900">{row.metric}</td>
									<td class="px-4 py-2 text-neutral-700">{row.a_mean !== null ? row.a_mean.toFixed(3) : '—'}</td>
									<td class="px-4 py-2 text-neutral-700">{row.b_mean !== null ? row.b_mean.toFixed(3) : '—'}</td>
									<td class="px-4 py-2 font-medium {deltaClass(row.delta)}">{formatDelta(row.delta)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	</div>
{/if}
