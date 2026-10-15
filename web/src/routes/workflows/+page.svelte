<script lang="ts">
	import { onMount } from 'svelte';
	import { listWorkflows } from '$lib/api/console';
	import type { Workflow } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	let workflows = $state<Workflow[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showArchived = $state(false);

	async function load() {
		loading = true;
		error = null;
		try {
			workflows = await listWorkflows();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load workflows.';
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
		headerContent.set({ menu: 'Workflows', title: 'Workflows', actions: newWorkflowAction });
	});

	let filtered = $derived(workflows.filter((w) => showArchived || w.status !== 'archived'));
</script>

{#snippet newWorkflowAction()}
	<a
		href="/workflows/new"
		class="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-white
			shadow-sm hover:bg-primary-600"
	>
		+ New workflow
	</a>
{/snippet}

<div class="mb-4 flex items-center gap-4">
	<label class="flex items-center gap-2 text-sm text-neutral-600">
		<input type="checkbox" bind:checked={showArchived} />
		Show archived
	</label>
</div>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading workflows…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load workflows</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if filtered.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">
			No workflows yet for this company — a workflow orchestrates multiple published Agents
			through a graph of states (PRD §6.9).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Status</th>
					<th class="px-4 py-2">Updated</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each filtered as workflow (workflow.id)}
					<tr>
						<td class="px-4 py-2 font-medium text-neutral-900">
							<a href="/workflows/{workflow.id}" class="text-primary underline">{workflow.name}</a>
						</td>
						<td class="px-4 py-2 text-neutral-600">{workflow.status}</td>
						<td class="px-4 py-2 text-neutral-600">{new Date(workflow.updated_at).toLocaleString()}</td>
						<td class="px-4 py-2 text-right">
							<a
								href="/workflows/{workflow.id}"
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
