<script lang="ts">
	// Generic daily line-trend chart (dataviz skill: 2px lines, a crosshair+tooltip hover
	// layer, a legend whenever there's >1 series, gaps left open rather than
	// interpolated across a day with no data - e.g. a quiet day has no runs, so no
	// latency percentile exists for it, and drawing a line through that gap would imply
	// a value that was never measured). One axis only - every series passed in must
	// already share the same unit/scale (latency's two percentiles, or cost on its own);
	// never mix e.g. latency and cost on one instance of this component.
	interface Series {
		key: string;
		label: string;
		strokeClass: string;
		dotFillClass: string;
	}

	interface Props {
		data: Array<Record<string, unknown> & { date: string }>;
		series: Series[];
		formatDayLabel: (date: string) => string;
		formatFullDate: (date: string) => string;
		ariaLabel: string;
		valueFormatter?: (value: number) => string;
	}

	let { data, series, formatDayLabel, formatFullDate, ariaLabel, valueFormatter = (v) => v.toLocaleString() }: Props =
		$props();

	const CHART_WIDTH = 700;
	const CHART_HEIGHT = 160;
	const CHART_TOP_PADDING = 8;
	const LABEL_SPACE = 20;
	const SVG_HEIGHT = CHART_HEIGHT + LABEL_SPACE;

	let hoveredIndex = $state<number | null>(null);

	function valueOf(row: Record<string, unknown>, key: string): number | null {
		const v = row[key];
		return v === null || v === undefined ? null : Number(v);
	}

	let maxValue = $derived(
		Math.max(
			1,
			...data.flatMap((row) => series.map((s) => valueOf(row, s.key)).filter((v): v is number => v !== null))
		)
	);

	function pointX(index: number): number {
		if (data.length <= 1) return CHART_WIDTH / 2;
		return (index / (data.length - 1)) * CHART_WIDTH;
	}

	function pointY(value: number): number {
		const plotHeight = CHART_HEIGHT - CHART_TOP_PADDING;
		return CHART_HEIGHT - (value / maxValue) * plotHeight;
	}

	// Breaks the path (a fresh "M" instead of "L") whenever a null interrupts the
	// series, instead of silently skipping the point and connecting across the gap.
	function linePath(seriesKey: string): string {
		let d = '';
		let drawing = false;
		data.forEach((row, i) => {
			const value = valueOf(row, seriesKey);
			if (value === null) {
				drawing = false;
				return;
			}
			const x = pointX(i);
			const y = pointY(value);
			d += drawing ? ` L${x},${y}` : `${d ? ' ' : ''}M${x},${y}`;
			drawing = true;
		});
		return d;
	}
</script>

{#if series.length > 1}
	<div class="mb-2 flex flex-wrap gap-3 text-xs text-neutral-600">
		{#each series as s (s.key)}
			<span class="flex items-center gap-1.5">
				<span class="h-0.5 w-3 rounded-full {s.dotFillClass}" aria-hidden="true"></span>
				{s.label}
			</span>
		{/each}
	</div>
{/if}

<div class="relative">
	<svg
		viewBox="0 0 {CHART_WIDTH} {SVG_HEIGHT}"
		class="w-full"
		style="height: {SVG_HEIGHT}px;"
		role="img"
		aria-label={ariaLabel}
	>
		<line x1="0" y1={CHART_HEIGHT} x2={CHART_WIDTH} y2={CHART_HEIGHT} class="stroke-neutral-200" stroke-width="1" />
		{#each series as s (s.key)}
			<path d={linePath(s.key)} fill="none" class={s.strokeClass} stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
		{/each}

		{#if hoveredIndex !== null}
			<line
				x1={pointX(hoveredIndex)}
				y1="0"
				x2={pointX(hoveredIndex)}
				y2={CHART_HEIGHT}
				class="stroke-neutral-300"
				stroke-width="1"
			/>
			{#each series as s (s.key)}
				{@const value = valueOf(data[hoveredIndex], s.key)}
				{#if value !== null}
					<circle cx={pointX(hoveredIndex)} cy={pointY(value)} r="4" class={s.dotFillClass} />
				{/if}
			{/each}
		{/if}

		<!-- One invisible hit column per day, full chart height, rather than a target on
			the thin 2px line itself - matches the dataviz skill's "hit targets bigger than
			the mark" rule. -->
		{#each data as row, i (row.date)}
			{@const bandWidth = CHART_WIDTH / data.length}
			<rect
				x={i * bandWidth}
				y="0"
				width={bandWidth}
				height={CHART_HEIGHT}
				fill="transparent"
				role="button"
				tabindex="0"
				aria-label="{formatFullDate(row.date)}: {series
					.map((s) => {
						const v = valueOf(row, s.key);
						return `${s.label} ${v === null ? 'no data' : valueFormatter(v)}`;
					})
					.join(', ')}"
				onmouseenter={() => (hoveredIndex = i)}
				onmouseleave={() => (hoveredIndex = null)}
				onfocus={() => (hoveredIndex = i)}
				onblur={() => (hoveredIndex = null)}
			/>
			<text x={i * bandWidth + bandWidth / 2} y={CHART_HEIGHT + 14} text-anchor="middle" class="fill-neutral-400 text-[9px]">
				{formatDayLabel(row.date)}
			</text>
		{/each}
	</svg>

	{#if hoveredIndex !== null}
		{@const row = data[hoveredIndex]}
		<div
			class="pointer-events-none absolute -translate-y-full rounded-md border border-neutral-200 bg-white px-2.5
				py-1.5 text-xs shadow-md"
			style="left: {(pointX(hoveredIndex) / CHART_WIDTH) * 100}%; top: {(Math.min(
				...series.map((s) => {
					const v = valueOf(row, s.key);
					return v === null ? CHART_HEIGHT : pointY(v);
				})
			) /
				SVG_HEIGHT) *
				100}%; transform: translateX(-50%) translateY(-100%);"
		>
			<p class="font-semibold text-neutral-900">{formatFullDate(row.date)}</p>
			{#each series as s (s.key)}
				{@const value = valueOf(row, s.key)}
				<p class="flex items-center justify-between gap-3 text-neutral-600">
					<span class="flex items-center gap-1.5">
						<span class="h-0.5 w-3 rounded-full {s.dotFillClass}" aria-hidden="true"></span>
						{s.label}
					</span>
					<span class="font-medium text-neutral-900">{value === null ? '—' : valueFormatter(value)}</span>
				</p>
			{/each}
		</div>
	{/if}
</div>
