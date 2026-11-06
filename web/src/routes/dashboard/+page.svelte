<script lang="ts">
	import { onMount } from 'svelte';
	import { getDashboardSummary } from '$lib/api/console';
	import type { DashboardAlertType, DashboardSummary } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';
	import StackedBarChart from '$lib/components/charts/StackedBarChart.svelte';
	import TrendLineChart from '$lib/components/charts/TrendLineChart.svelte';

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
	// Reload whenever the company switcher changes — every figure here is per-company.
	$effect(() => {
		$companyId;
		load();
	});

	interface Tile {
		label: string;
		value: string;
		hint?: string;
	}

	function formatDayLabel(dateStr: string): string {
		return String(Number(dateStr.slice(8, 10))); // "2026-10-23" -> "23"
	}

	function formatFullDate(dateStr: string): string {
		return new Date(dateStr + 'T00:00:00').toLocaleDateString(undefined, {
			weekday: 'short',
			month: 'short',
			day: 'numeric'
		});
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

	const ALERT_TYPE_LABELS: Record<DashboardAlertType, string> = {
		quota: 'Budget',
		error_rate: 'Error rate',
		guardrail: 'Guardrail',
		latency: 'Latency',
		cost: 'Cost'
	};

	// --- Series/color definitions for the charts below -----------------------------------
	// Reuses this app's own existing status-color convention (web/src/routes/runs/
	// +page.svelte's statusStyles) rather than inventing a fresh palette - success is the
	// brand teal everywhere else in this app already uses for "success", error/blocked/
	// guardrail-block share the one danger red this design system has, same for warning.
	// "Other" (interrupted/truncated/cancelled, folded together - see
	// DashboardController.php's own comment) and the two token directions get a
	// light/dark pair of the one hue available rather than a fabricated new categorical
	// color this admin tool's limited palette was never validated for.
	const RUN_STATUS_SERIES = [
		{ key: 'success', label: 'Success', fillClass: 'fill-primary-500', hoverFillClass: 'fill-primary-600' },
		{ key: 'error', label: 'Error', fillClass: 'fill-danger', hoverFillClass: 'fill-danger-dark' },
		{ key: 'blocked', label: 'Blocked', fillClass: 'fill-warning', hoverFillClass: 'fill-warning-dark' },
		{ key: 'other', label: 'Other', fillClass: 'fill-neutral-300', hoverFillClass: 'fill-neutral-400' }
	];

	// primary-500/-700 only - tailwind.config.ts's primary ramp stops at 700, no -300/-900
	// step exists to draw a lighter/darker pair from.
	const TOKEN_DIRECTION_SERIES = [
		{ key: 'tokens_in', label: 'Tokens in', fillClass: 'fill-primary-500', hoverFillClass: 'fill-primary-600' },
		{ key: 'tokens_out', label: 'Tokens out', fillClass: 'fill-primary-700', hoverFillClass: 'fill-primary-700' }
	];

	const GUARDRAIL_ACTION_SERIES = [
		{ key: 'block', label: 'Blocked', fillClass: 'fill-danger', hoverFillClass: 'fill-danger-dark' },
		{ key: 'mask', label: 'Masked', fillClass: 'fill-warning', hoverFillClass: 'fill-warning-dark' },
		{ key: 'flag', label: 'Flagged', fillClass: 'fill-primary-500', hoverFillClass: 'fill-primary-600' }
	];

	// Light -> dark by severity (p50 lightest/least severe, p99 darkest/most severe) -
	// matches the 3 percentiles the external Grafana dashboard showed, now shown here
	// instead (PRD direction as of 2026-11-06: no longer depend on Grafana).
	const LATENCY_SERIES = [
		{ key: 'p50_ms', label: 'p50', strokeClass: 'stroke-primary-500', dotFillClass: 'fill-primary-500' },
		{ key: 'p95_ms', label: 'p95', strokeClass: 'stroke-primary-600', dotFillClass: 'fill-primary-600' },
		{ key: 'p99_ms', label: 'p99', strokeClass: 'stroke-primary-700', dotFillClass: 'fill-primary-700' }
	];

	const COST_SERIES = [
		{ key: 'cost_usd', label: 'Cost', strokeClass: 'stroke-primary-500', dotFillClass: 'fill-primary-500' }
	];

	function formatMs(value: number): string {
		return `${Math.round(value).toLocaleString()} ms`;
	}

	function formatUsd(value: number): string {
		return `$${value.toFixed(4)}`;
	}
</script>

<p class="mb-4 text-sm text-neutral-600">
	A snapshot of this company's activity — runs today, token and cost totals for the month, error
	rate, guardrail triggers, and 14-day trends for run volume, latency, token/cost usage, and
	guardrail actions. Replaces the external Grafana dashboard — everything here is computed
	directly from this company's own data, nothing routes through Grafana/Prometheus.
</p>

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
	{#if summary.alerts.length > 0}
		<div class="mb-4 space-y-2">
			{#each summary.alerts as alert (alert.type)}
				<div class="flex items-start gap-3 rounded-md border border-warning/30 bg-warning/5 p-3">
					<span class="mt-0.5 shrink-0 rounded-full bg-warning/20 px-2 py-0.5 text-xs font-medium text-warning-dark">
						{ALERT_TYPE_LABELS[alert.type]}
					</span>
					<p class="text-sm text-neutral-700">{alert.message}</p>
				</div>
			{/each}
		</div>
	{/if}

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
		<p class="text-sm font-medium text-neutral-700">Runs per day by status (last 14 days)</p>
		{#if summary.runs_per_day.every((d) => d.success + d.error + d.blocked + d.other === 0)}
			<p class="mt-3 text-sm text-neutral-400">No runs in this window yet.</p>
		{:else}
			<div class="mt-3">
				<StackedBarChart
					data={summary.runs_per_day}
					series={RUN_STATUS_SERIES}
					{formatDayLabel}
					{formatFullDate}
					unit=" runs"
					ariaLabel="Runs per day by status, last {summary.runs_per_day.length} days"
				/>
			</div>
		{/if}
	</div>

	<div class="mt-6 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<p class="text-sm font-medium text-neutral-700">Latency p50/p95/p99 (last 14 days)</p>
		{#if summary.latency_per_day.every((d) => d.p50_ms === null)}
			<p class="mt-3 text-sm text-neutral-400">No completed runs with latency recorded in this window yet.</p>
		{:else}
			<div class="mt-3">
				<TrendLineChart
					data={summary.latency_per_day}
					series={LATENCY_SERIES}
					{formatDayLabel}
					{formatFullDate}
					valueFormatter={formatMs}
					ariaLabel="Run latency p50, p95, and p99, last {summary.latency_per_day.length} days"
				/>
			</div>
		{/if}
	</div>

	<div class="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
		<div class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<p class="text-sm font-medium text-neutral-700">Tokens per day (last 14 days)</p>
			{#if summary.tokens_cost_per_day.every((d) => d.tokens_in + d.tokens_out === 0)}
				<p class="mt-3 text-sm text-neutral-400">No token usage in this window yet.</p>
			{:else}
				<div class="mt-3">
					<StackedBarChart
						data={summary.tokens_cost_per_day}
						series={TOKEN_DIRECTION_SERIES}
						{formatDayLabel}
						{formatFullDate}
						ariaLabel="Tokens per day by direction, last {summary.tokens_cost_per_day.length} days"
					/>
				</div>
			{/if}
		</div>

		<div class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<p class="text-sm font-medium text-neutral-700">Cost per day (last 14 days)</p>
			{#if summary.tokens_cost_per_day.every((d) => d.cost_usd === 0)}
				<p class="mt-3 text-sm text-neutral-400">No cost recorded in this window yet.</p>
			{:else}
				<div class="mt-3">
					<TrendLineChart
						data={summary.tokens_cost_per_day}
						series={COST_SERIES}
						{formatDayLabel}
						{formatFullDate}
						valueFormatter={formatUsd}
						ariaLabel="Cost per day, last {summary.tokens_cost_per_day.length} days"
					/>
				</div>
			{/if}
		</div>
	</div>

	<div class="mt-6 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<p class="text-sm font-medium text-neutral-700">Guardrail actions per day (last 14 days)</p>
		{#if summary.guardrail_actions_per_day.every((d) => d.block + d.mask + d.flag === 0)}
			<p class="mt-3 text-sm text-neutral-400">No guardrail checks triggered in this window yet.</p>
		{:else}
			<div class="mt-3">
				<StackedBarChart
					data={summary.guardrail_actions_per_day}
					series={GUARDRAIL_ACTION_SERIES}
					{formatDayLabel}
					{formatFullDate}
					unit=" events"
					ariaLabel="Guardrail actions per day, last {summary.guardrail_actions_per_day.length} days"
				/>
			</div>
		{/if}
	</div>

	<div class="mt-6 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<p class="text-sm font-medium text-neutral-700">Top agents/workflows (last 14 days)</p>
		{#if summary.top_agents.length === 0}
			<p class="mt-3 text-sm text-neutral-400">No runs in this window yet.</p>
		{:else}
			<table class="mt-3 w-full text-sm">
				<thead class="text-left text-xs uppercase tracking-wide text-neutral-500">
					<tr>
						<th class="py-1">Agent / workflow</th>
						<th class="py-1 text-right">Runs</th>
						<th class="py-1 text-right">Cost</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-neutral-100">
					{#each summary.top_agents as agent (agent.name)}
						<tr>
							<td class="py-1.5 text-neutral-800">{agent.name}</td>
							<td class="py-1.5 text-right font-medium text-neutral-900">{agent.run_count.toLocaleString()}</td>
							<td class="py-1.5 text-right text-neutral-600">${agent.cost_usd.toFixed(4)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</div>
{/if}
