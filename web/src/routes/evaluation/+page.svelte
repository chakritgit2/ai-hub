<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { listEvalRunsForTenant } from '$lib/api/console';
	import type { EvalRun } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId, tenantId } from '$lib/stores/company';
	import { currentUser, companyLabelForCompany } from '$lib/stores/auth';

	// Pilot of "each menu shows only its own tenant's data" (option A) - pools eval runs
	// across every company under the selected tenant instead of the usual single
	// X-Company-Id company. See EvalsController::listEvalRuns()'s X-Tenant-Id branch and
	// agents/+page.svelte, the first menu this pattern was applied to.

	let evalRuns = $state<EvalRun[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Compare (PRD §6.7) always takes exactly two runs - checking a third silently drops
	// the oldest selection rather than showing a validation error, same low-ceremony
	// spirit as the rest of this page.
	const MAX_COMPARE_SELECTION = 2;
	let selectedIds = $state<string[]>([]);

	// compareEvalRuns() is still X-Company-Id-scoped (both runs looked up against the one
	// current company) - a pooled list can show runs from several companies, but comparing
	// across companies isn't something the backend supports yet, so picking a run from a
	// different company than what's already selected replaces the selection instead of
	// adding to it, same "silently drop the older pick" spirit as the 3rd-pick case above.
	function toggleSelected(run: EvalRun, checked: boolean) {
		if (checked) {
			const stillSameCompany = selectedIds
				.map((id) => evalRuns.find((r) => r.id === id))
				.every((r) => r?.company_id === run.company_id);
			const base = stillSameCompany ? selectedIds : [];
			selectedIds = [...base.filter((existing) => existing !== run.id), run.id].slice(-MAX_COMPARE_SELECTION);
		} else {
			selectedIds = selectedIds.filter((existing) => existing !== run.id);
		}
	}

	function compareSelected() {
		if (selectedIds.length !== MAX_COMPARE_SELECTION) {
			return;
		}
		const [a, b] = selectedIds;
		const run = evalRuns.find((r) => r.id === a);
		if (run) companyId.set(run.company_id);
		goto(`/evaluation/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
	}

	async function load() {
		loading = true;
		error = null;
		// No tenant resolved yet (e.g. a brand new SSO user with zero company
		// memberships at all, so CompanySwitcher never has anything to pick) - nothing
		// to fetch, but still clear the spinner rather than spinning forever. Same guard
		// as agents/+page.svelte's load().
		if (!$tenantId) {
			evalRuns = [];
			loading = false;
			return;
		}
		try {
			evalRuns = await listEvalRunsForTenant($tenantId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load eval runs.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$tenantId;
		// Switching tenants (CompanySwitcher) replaces evalRuns wholesale - a stale
		// selection from the previous tenant would otherwise survive (its ids not
		// found in the new evalRuns, so compareSelected()'s companyId.set() silently
		// no-ops and the app navigates to /evaluation/compare with a stale/unrelated
		// companyId still set).
		selectedIds = [];
		load();
	});

	// A pooled eval run can belong to any company under the tenant, not just whichever one
	// $companyId currently points at - the detail page still scopes strictly by
	// $companyId, so it must be pointed at this run's own company before navigating into
	// it, or the detail page 404s. Same approach as agents/+page.svelte's openAgent().
	function openEvalRun(run: EvalRun) {
		companyId.set(run.company_id);
	}

	function companyLabel(runCompanyId: string): string | undefined {
		return companyLabelForCompany($currentUser, runCompanyId);
	}

	$effect(() => {
		headerContent.set({ menu: 'Evaluation', title: 'Evaluation', actions: headerActions });
	});

	const statusStyles: Record<string, string> = {
		completed: 'bg-primary/10 text-primary-700',
		failed: 'bg-danger/10 text-danger',
		running: 'bg-warning/10 text-warning-dark',
		queued: 'bg-neutral-100 text-neutral-600',
		cancelled: 'bg-neutral-200 text-neutral-700'
	};

	function summarySnippet(run: EvalRun): string {
		// A cancelled run can still carry a real partial summary (whatever finished before
		// the cancel landed) - same data shape evaluation/[id]'s detail page already shows
		// tiles for, so this list view shouldn't flatten it to "—" just because the status
		// isn't "completed".
		if ((run.status !== 'completed' && run.status !== 'cancelled') || !run.summary) {
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
			No eval runs yet for this tenant — pick an agent version, a dataset, and metrics to
			start one (PRD §6.7).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2"></th>
					<th class="px-4 py-2">Company</th>
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
								onchange={(e) => toggleSelected(run, (e.currentTarget as HTMLInputElement).checked)}
							/>
						</td>
						<td class="px-4 py-2 text-neutral-600">
							{#if companyLabel(run.company_id)}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
									{companyLabel(run.company_id)}
								</span>
							{/if}
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
								onclick={() => openEvalRun(run)}
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
