<script lang="ts">
	// Meter spec (dataviz skill): fill carries severity (accent -> warning -> danger as
	// the ratio to its limit climbs); the track is a neutral, lighter surface so state
	// reads across the whole bar, not just the filled portion. With no limit configured
	// there's nothing to show a ratio against, so this renders a plain value instead.
	let {
		label,
		value,
		limit,
		unit,
		format = (n: number) => n.toLocaleString()
	}: {
		label: string;
		value: number;
		limit: number | null;
		unit: string;
		format?: (n: number) => string;
	} = $props();

	let ratio = $derived(limit && limit > 0 ? Math.min(1, value / limit) : null);
	let severity = $derived(ratio === null ? 'none' : ratio >= 0.9 ? 'critical' : ratio >= 0.7 ? 'warning' : 'good');
	let fillClass = $derived(
		severity === 'critical' ? 'bg-danger' : severity === 'warning' ? 'bg-warning' : 'bg-primary-500'
	);
</script>

<div>
	<p class="text-xs text-neutral-400">{label}</p>
	<p class="mt-0.5 text-sm font-medium text-neutral-900">
		{format(value)}
		{unit}
		{#if limit !== null}
			<span class="font-normal text-neutral-400">/ {format(limit)} {unit}</span>
		{/if}
	</p>
	{#if ratio !== null}
		<div class="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100" role="progressbar" aria-label="{label}: {format(value)} of {format(limit ?? 0)} {unit}" aria-valuenow={value} aria-valuemin={0} aria-valuemax={limit ?? 0}>
			<div class="h-full rounded-full {fillClass}" style="width: {ratio * 100}%"></div>
		</div>
	{/if}
</div>
