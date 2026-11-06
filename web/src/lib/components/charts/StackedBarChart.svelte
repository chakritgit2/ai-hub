<script lang="ts">
	// Generic daily stacked-bar chart (dataviz skill: bars <=24px thick, 4px rounded
	// data-end on the topmost segment only, square baseline, 2px surface gap between
	// stacked segments, legend present whenever there's >1 series, hover tooltip with a
	// bigger invisible hit target than the visible bar). Built once here and reused for
	// every daily-breakdown chart on the Dashboard page (run status, token direction,
	// guardrail action) instead of hand-rolling the same SVG geometry three times.
	interface Series {
		key: string;
		label: string;
		fillClass: string;
		hoverFillClass: string;
	}

	interface Props {
		data: Array<Record<string, unknown> & { date: string }>;
		series: Series[];
		formatDayLabel: (date: string) => string;
		formatFullDate: (date: string) => string;
		ariaLabel: string;
		unit?: string;
	}

	let { data, series, formatDayLabel, formatFullDate, ariaLabel, unit = '' }: Props = $props();

	const CHART_WIDTH = 700;
	const CHART_HEIGHT = 160;
	const CHART_TOP_PADDING = 8;
	const LABEL_SPACE = 20;
	const SVG_HEIGHT = CHART_HEIGHT + LABEL_SPACE;
	const SEGMENT_GAP = 2;

	let hoveredIndex = $state<number | null>(null);

	function valueOf(row: Record<string, unknown>, key: string): number {
		return Number(row[key]) || 0;
	}

	function totalOf(row: Record<string, unknown>): number {
		return series.reduce((sum, s) => sum + valueOf(row, s.key), 0);
	}

	let maxTotal = $derived(Math.max(1, ...data.map(totalOf)));

	interface Segment {
		seriesIndex: number;
		x: number;
		y: number;
		width: number;
		height: number;
		radius: number;
		isTop: boolean;
	}

	interface Bar {
		date: string;
		x: number;
		width: number;
		total: number;
		segments: Segment[];
	}

	let bars = $derived<Bar[]>(
		data.map((row) => {
			const bandWidth = CHART_WIDTH / data.length;
			const barWidth = Math.min(24, bandWidth - 2);
			const x = data.indexOf(row) * bandWidth + (bandWidth - barWidth) / 2;
			const plotHeight = CHART_HEIGHT - CHART_TOP_PADDING;

			let yCursor = CHART_HEIGHT;
			const segments: Segment[] = [];
			series.forEach((s, seriesIndex) => {
				const value = valueOf(row, s.key);
				if (value <= 0) return;
				const height = (value / maxTotal) * plotHeight;
				const y = yCursor - height;
				segments.push({ seriesIndex, x, y, width: barWidth, height, radius: Math.min(4, height), isTop: false });
				yCursor = y - SEGMENT_GAP;
			});
			if (segments.length > 0) segments[segments.length - 1].isTop = true;

			return { date: row.date, x, width: barWidth, total: totalOf(row), segments };
		})
	);

	// Square-baseline rect for a middle/bottom segment, top-rounded-only path for the
	// topmost non-zero segment of a bar (a plain rx on a <rect> would round every
	// corner, putting a curve at segment boundaries the spec says should stay square).
	function segmentPath(seg: Segment): string {
		const { x, y, width: w, height: h, radius: r, isTop } = seg;
		if (h <= 0) return '';
		if (!isTop) {
			return `M${x},${y} L${x + w},${y} L${x + w},${y + h} L${x},${y + h} Z`;
		}
		return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
	}

	function barAriaLabel(bar: Bar): string {
		const parts = series
			.map((s, i) => {
				const seg = bar.segments.find((seg) => seg.seriesIndex === i);
				const value = valueOf(data.find((r) => r.date === bar.date) ?? {}, s.key);
				return `${s.label} ${value}`;
			})
			.join(', ');
		return `${formatFullDate(bar.date)}: ${parts}`;
	}
</script>

{#if series.length > 1}
	<div class="mb-2 flex flex-wrap gap-3 text-xs text-neutral-600">
		{#each series as s (s.key)}
			<span class="flex items-center gap-1.5">
				<span class="h-2.5 w-2.5 rounded-sm {s.fillClass}" aria-hidden="true"></span>
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
		{#each bars as bar, i (bar.date)}
			{#each bar.segments as seg (seg.seriesIndex)}
				<path
					d={segmentPath(seg)}
					class={hoveredIndex === i ? series[seg.seriesIndex].hoverFillClass : series[seg.seriesIndex].fillClass}
				/>
			{/each}
			<!-- Invisible hit target spanning the full bar height (not just the drawn
				segments) - a day with every series at 0 would otherwise have nothing to
				hover at all, and a thin single-segment bar would be hard to target. -->
			<rect
				x={bar.x}
				y="0"
				width={bar.width}
				height={CHART_HEIGHT}
				fill="transparent"
				role="button"
				tabindex="0"
				aria-label={barAriaLabel(bar)}
				onmouseenter={() => (hoveredIndex = i)}
				onmouseleave={() => (hoveredIndex = null)}
				onfocus={() => (hoveredIndex = i)}
				onblur={() => (hoveredIndex = null)}
			/>
			<text x={bar.x + bar.width / 2} y={CHART_HEIGHT + 14} text-anchor="middle" class="fill-neutral-400 text-[9px]">
				{formatDayLabel(bar.date)}
			</text>
		{/each}
	</svg>

	{#if hoveredIndex !== null}
		{@const bar = bars[hoveredIndex]}
		{@const row = data[hoveredIndex]}
		<div
			class="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-md border
				border-neutral-200 bg-white px-2.5 py-1.5 text-xs shadow-md"
			style="left: {((bar.x + bar.width / 2) / CHART_WIDTH) * 100}%; top: {(Math.min(
				...bar.segments.map((s) => s.y),
				CHART_HEIGHT
			) /
				SVG_HEIGHT) *
				100}%;"
		>
			<p class="font-semibold text-neutral-900">{formatFullDate(bar.date)}</p>
			{#each series as s (s.key)}
				<p class="flex items-center justify-between gap-3 text-neutral-600">
					<span class="flex items-center gap-1.5">
						<span class="h-2 w-2 rounded-sm {s.fillClass}" aria-hidden="true"></span>
						{s.label}
					</span>
					<span class="font-medium text-neutral-900">{valueOf(row, s.key).toLocaleString()}{unit}</span>
				</p>
			{/each}
		</div>
	{/if}
</div>
