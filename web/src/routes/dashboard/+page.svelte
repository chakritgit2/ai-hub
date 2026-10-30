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

	// --- Runs-per-day chart geometry (dataviz skill: bars <=24px thick, 4px rounded
	// data-end/square baseline, 2px surface gap between bars, single series so no legend,
	// label selectively rather than stamping every bar) ---------------------------------
	const CHART_WIDTH = 700;
	const CHART_HEIGHT = 160; // baseline for bar geometry - day labels render below this
	const CHART_TOP_PADDING = 8;
	const LABEL_SPACE = 20; // extra room below the baseline so day labels aren't clipped by the viewBox
	const SVG_HEIGHT = CHART_HEIGHT + LABEL_SPACE;

	let hoveredIndex = $state<number | null>(null);
	let showRunsTable = $state(false);

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

	let runsPerDay = $derived(summary?.runs_per_day ?? []);
	let maxRuns = $derived(Math.max(1, ...runsPerDay.map((d) => d.count)));

	interface Bar {
		x: number;
		width: number;
		height: number;
		y: number;
		radius: number;
		date: string;
		count: number;
	}

	let bars = $derived<Bar[]>(
		runsPerDay.map((d, i) => {
			const bandWidth = CHART_WIDTH / runsPerDay.length;
			const barWidth = Math.min(24, bandWidth - 2);
			const x = i * bandWidth + (bandWidth - barWidth) / 2;
			const plotHeight = CHART_HEIGHT - CHART_TOP_PADDING;
			const height = (d.count / maxRuns) * plotHeight;
			const radius = Math.min(4, height);
			return {
				x,
				width: barWidth,
				height,
				y: CHART_HEIGHT - height,
				radius,
				date: d.date,
				count: d.count
			};
		})
	);

	// Top-rounded, square-baseline bar as a path (a plain rx on a <rect> would round
	// every corner, putting a curve at the baseline the spec says should stay square).
	function barPath(bar: Bar): string {
		const { x, y, width: w, height: h, radius: r } = bar;
		if (h <= 0) return '';
		return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
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
		<div class="flex items-center justify-between">
			<p class="text-sm font-medium text-neutral-700">Runs per day (last 14 days)</p>
			<button
				type="button"
				class="text-xs text-neutral-500 underline-offset-2 hover:text-neutral-700 hover:underline"
				onclick={() => (showRunsTable = !showRunsTable)}
			>
				{showRunsTable ? 'Show chart' : 'Show as table'}
			</button>
		</div>

		{#if runsPerDay.length === 0}
			<p class="mt-3 text-sm text-neutral-400">No runs in this window yet.</p>
		{:else if showRunsTable}
			<table class="mt-3 w-full text-sm">
				<thead class="text-left text-xs uppercase tracking-wide text-neutral-500">
					<tr>
						<th class="py-1">Date</th>
						<th class="py-1 text-right">Runs</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-neutral-100">
					{#each runsPerDay as day (day.date)}
						<tr>
							<td class="py-1 text-neutral-700">{formatFullDate(day.date)}</td>
							<td class="py-1 text-right font-medium text-neutral-900">{day.count.toLocaleString()}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			<div class="relative mt-3">
				<svg
					viewBox="0 0 {CHART_WIDTH} {SVG_HEIGHT}"
					class="w-full"
					style="height: {SVG_HEIGHT}px;"
					role="img"
					aria-label="Runs per day, last {runsPerDay.length} days"
				>
					<!-- Baseline (recessive hairline, per dataviz skill - gridlines never dashed). -->
					<line
						x1="0"
						y1={CHART_HEIGHT}
						x2={CHART_WIDTH}
						y2={CHART_HEIGHT}
						class="stroke-neutral-200"
						stroke-width="1"
					/>
					{#each bars as bar, i (bar.date)}
						<path
							d={barPath(bar)}
							class={hoveredIndex === i ? 'fill-primary-600' : 'fill-primary-500'}
							role="button"
							tabindex="0"
							aria-label="{formatFullDate(bar.date)}: {bar.count} runs"
							onmouseenter={() => (hoveredIndex = i)}
							onmouseleave={() => (hoveredIndex = null)}
							onfocus={() => (hoveredIndex = i)}
							onblur={() => (hoveredIndex = null)}
						/>
						<text
							x={bar.x + bar.width / 2}
							y={CHART_HEIGHT + 14}
							text-anchor="middle"
							class="fill-neutral-400 text-[9px]"
						>
							{formatDayLabel(bar.date)}
						</text>
					{/each}
				</svg>

				{#if hoveredIndex !== null}
					{@const bar = bars[hoveredIndex]}
					<div
						class="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-md border
							border-neutral-200 bg-white px-2 py-1 text-xs shadow-md"
						style="left: {((bar.x + bar.width / 2) / CHART_WIDTH) * 100}%; top: {(bar.y / SVG_HEIGHT) * 100}%;"
					>
						<span class="font-semibold text-neutral-900">{bar.count.toLocaleString()}</span>
						<span class="text-neutral-500"> runs · {formatFullDate(bar.date)}</span>
					</div>
				{/if}
			</div>
		{/if}
	</div>
{/if}
