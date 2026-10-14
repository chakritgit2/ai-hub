<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { listEvalRuns } from '$lib/api/console';
	import type { EvalRun } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	let evalRuns = $state<EvalRun[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Compare (PRD §6.7) always takes exactly two runs - checking a third silently drops
	// the oldest selection rather than showing a validation error, same low-ceremony
	// spirit as the rest of this page.
	const MAX_COMPARE_SELECTION = 2;
	let selectedIds = $state<string[]>([]);

	function toggleSelected(id: string, checked: boolean) {
		if (checked) {
			selectedIds = [...selectedIds.filter((existing) => existing !== id), id].slice(-MAX_COMPARE_SELECTION);
		} else {
			selectedIds = selectedIds.filter((existing) => existing !== id);
		}
	}

	function compareSelected() {
		if (selectedIds.length !== MAX_COMPARE_SELECTION) {
			return;
		}
		const [a, b] = selectedIds;
		goto(`/evaluation/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
	}

	async function load() {
		loading = true;
		error = null;
		try {
			evalRuns = await listEvalRuns();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load eval runs.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$companyId;
		load();
	});

	$effect(() => {
		headerContent.set({ menu: 'Evaluation', title: 'Evaluation', actions: headerActions });
	});

	const statusStyles: Record<string, string> = {
		completed: 'bg-primary/10 text-primary-700',
		failed: 'bg-danger/10 text-danger',
		running: 'bg-warning/10 text-warning-dark',
		queued: 'bg-neutral-100 text-neutral-600'
	};

	function summarySnippet(run: EvalRun): string {
		if (run.status !== 'completed' || !run.summary) {
			return '—';
		}
		const scores = Object.entries(run.summary.mean_scores);
		if (scores.length === 0) {
			return `${run.summary.item_count} item${run.summary.item_count === 1 ? '' : 's'}`;
		}
		return scores.map(([metric, score]) => `${metric}: ${score.toFixed(2)}`).join(', ');
	}
</script>

{#snippet headerActions()}
	<div class="flex gap-2">
		<button
			type="button"
			disabled={selectedIds.length !== MAX_COMPARE_SELECTION}
			onclick={compareSelected}
			class="rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50
				disabled:cursor-not-allowed disabled:opacity-50"
		>
			Compare selected
		</button>
		<a
			href="/evaluation/new"
			class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		>
			+ New eval run
		</a>
	</div>
{/snippet}

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading eval runs…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load eval runs</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if evalRuns.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">
			No eval runs yet for this company — pick an agent version, a dataset, and metrics to
			start one (PRD §6.7).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2"></th>
					<th class="px-4 py-2">Created</th>
					<th class="px-4 py-2">Status</th>
					<th class="px-4 py-2">Metrics</th>
					<th class="px-4 py-2">Summary</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each evalRuns as run (run.id)}
					<tr>
						<td class="px-4 py-2">
							<input
								type="checkbox"
								aria-label="Select {run.id} for comparison"
								checked={selectedIds.includes(run.id)}
								onchange={(e) => toggleSelected(run.id, (e.currentTarget as HTMLInputElement).checked)}
							/>
						</td>
						<td class="px-4 py-2 text-neutral-600">{new Date(run.created_at).toLocaleString()}</td>
						<td class="px-4 py-2">
							<span
								class="rounded-full px-2 py-0.5 text-xs font-medium {statusStyles[run.status] ??
									'bg-neutral-100 text-neutral-600'}"
							>
								{run.status}
							</span>
						</td>
						<td class="px-4 py-2 text-neutral-700">{run.metrics.join(', ')}</td>
						<td class="px-4 py-2 text-neutral-600">{summarySnippet(run)}</td>
						<td class="px-4 py-2 text-right">
							<a
								href="/evaluation/{run.id}"
								class="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
							>
								View
							</a>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
