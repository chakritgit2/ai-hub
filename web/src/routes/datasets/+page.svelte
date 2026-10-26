<script lang="ts">
	import { onMount } from 'svelte';
	import { listDatasetsForTenant, createDataset } from '$lib/api/console';
	import type { Dataset } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId, tenantId } from '$lib/stores/company';
	import { currentUser, companyLabelForCompany } from '$lib/stores/auth';

	// Pilot of "each menu shows only its own tenant's data" (option A) - pools datasets
	// across every company under the selected tenant instead of the usual single
	// X-Company-Id company. See DatasetsController::listDatasets()'s X-Tenant-Id branch
	// and agents/+page.svelte, the first menu this pattern was applied to.

	let datasets = $state<Dataset[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showForm = $state(false);

	let form = $state({ name: '' });
	let formError = $state<string | null>(null);
	let submitting = $state(false);

	async function load() {
		loading = true;
		error = null;
		// No tenant resolved yet (e.g. a brand new SSO user with zero company
		// memberships at all, so CompanySwitcher never has anything to pick) - nothing
		// to fetch, but still clear the spinner rather than spinning forever. Same guard
		// as agents/+page.svelte's load().
		if (!$tenantId) {
			datasets = [];
			loading = false;
			return;
		}
		try {
			datasets = await listDatasetsForTenant($tenantId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load datasets.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$tenantId;
		load();
	});

	// A pooled dataset can belong to any company under the tenant, not just whichever one
	// $companyId currently points at - the detail page still scopes strictly by
	// $companyId, so it must be pointed at this dataset's own company before navigating
	// into it, or the detail page 404s. Same approach as agents/+page.svelte's openAgent().
	function openDataset(dataset: Dataset) {
		companyId.set(dataset.company_id);
	}

	function companyLabel(datasetCompanyId: string): string | undefined {
		return companyLabelForCompany($currentUser, datasetCompanyId);
	}

	$effect(() => {
		headerContent.set({ menu: 'Datasets', title: 'Datasets', actions: newDatasetAction });
	});

	function resetForm() {
		form = { name: '' };
	}

	async function submitForm() {
		submitting = true;
		formError = null;
		try {
			await createDataset({ name: form.name });
			showForm = false;
			resetForm();
			await load();
		} catch (e) {
			formError = e instanceof Error ? e.message : 'Failed to create the dataset.';
		} finally {
			submitting = false;
		}
	}
</script>

{#snippet newDatasetAction()}
	<button
		type="button"
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		onclick={() => (showForm = !showForm)}
	>
		+ New dataset
	</button>
{/snippet}

{#if showForm}
	<form
		class="mb-6 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm"
		onsubmit={(e) => {
			e.preventDefault();
			submitForm();
		}}
	>
		{#if formError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
				{formError}
			</div>
		{/if}
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Name</span>
			<input
				type="text"
				bind:value={form.name}
				required
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<div class="flex gap-2">
			<button
				type="submit"
				disabled={submitting}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
			>
				{submitting ? 'Saving…' : 'Save dataset'}
			</button>
			<button
				type="button"
				class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
				onclick={() => (showForm = false)}
			>
				Cancel
			</button>
		</div>
	</form>
{/if}

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading datasets…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load datasets</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if datasets.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">
			No datasets yet for this tenant — a dataset is a reusable set of test questions for
			Evaluation runs (PRD §6.7).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Company</th>
					<th class="px-4 py-2">Updated</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each datasets as dataset (dataset.id)}
					<tr>
						<td class="px-4 py-2 font-medium text-neutral-900">
							<a
								href="/datasets/{dataset.id}"
								onclick={() => openDataset(dataset)}
								class="text-primary underline"
							>
								{dataset.name}
							</a>
						</td>
						<td class="px-4 py-2 text-neutral-600">
							{#if companyLabel(dataset.company_id)}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
									{companyLabel(dataset.company_id)}
								</span>
							{/if}
						</td>
						<td class="px-4 py-2 text-neutral-600">{new Date(dataset.updated_at).toLocaleString()}</td>
						<td class="px-4 py-2 text-right">
							<a
								href="/datasets/{dataset.id}"
								onclick={() => openDataset(dataset)}
								class="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
							>
								Manage
							</a>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
