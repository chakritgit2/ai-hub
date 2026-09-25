<script lang="ts">
	import { onMount } from 'svelte';
	import { getDashboardSummary } from '$lib/api/console';
	import type { DashboardSummary } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	headerContent.set({ menu: 'Dashboard', title: 'Dashboard' });

	let summary = $state<DashboardSummary | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			summary = await getDashboardSummary();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load the dashboard summary.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	// Reload whenever the company switcher changes (PRD §7.2 — every figure is per-company).
	$effect(() => {
		$companyId;
		load();
	});

	interface Tile {
		label: string;
		value: string;
		hint?: string;
	}

	let tiles = $derived<Tile[]>(
		summary
			? [
					{ label: 'Runs today', value: summary.runs_today.toLocaleString() },
					{
						label: 'Tokens this month',
						value: `${summary.tokens_this_month.in.toLocaleString()} in / ${summary.tokens_this_month.out.toLocaleString()} out`
					},
					{
						label: 'Cost this month',
						value: `$${summary.cost_this_month_usd.toFixed(2)}`,
						hint: 'Estimate as of now'
					},
					{
						label: 'Error rate',
						value: `${(summary.error_rate * 100).toFixed(1)}%`
					},
					{
						label: 'Guardrail triggers',
						value: summary.guardrail_triggers.toLocaleString()
					}
				]
			: []
	);
</script>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading dashboard…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load the dashboard</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if !summary}
	<p class="text-sm text-neutral-500">No data yet for this company.</p>
{:else}
	<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
		{#each tiles as tile (tile.label)}
			<div class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
				<p class="text-xs font-medium uppercase tracking-wide text-neutral-500">{tile.label}</p>
				<p class="mt-2 text-2xl font-semibold text-neutral-900">{tile.value}</p>
				{#if tile.hint}
					<p class="mt-1 text-xs text-neutral-400">{tile.hint}</p>
				{/if}
			</div>
		{/each}
	</div>

	<div class="mt-6 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<p class="text-sm font-medium text-neutral-700">Runs per day (last 14 days)</p>
		<div
			class="mt-3 flex h-40 items-center justify-center rounded-md border border-dashed border-neutral-300 text-sm text-neutral-400"
		>
			Chart placeholder — wire up once /dashboard returns a daily series.
		</div>
	</div>
{/if}
