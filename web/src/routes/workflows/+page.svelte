<script lang="ts">
	import { onMount } from 'svelte';
	import { listWorkflowsForTenant } from '$lib/api/console';
	import type { Workflow } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId, tenantId } from '$lib/stores/company';
	import { currentUser, companyLabelForCompany } from '$lib/stores/auth';

	// Pilot of "each menu shows only its own tenant's data" (option A) - pools workflows
	// across every company under the selected tenant instead of the usual single
	// X-Company-Id company. See WorkflowsController::listWorkflows()'s X-Tenant-Id branch
	// and agents/+page.svelte, the first menu this pattern was applied to.

	let workflows = $state<Workflow[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showArchived = $state(false);

	async function load() {
		loading = true;
		error = null;
		// No tenant resolved yet (e.g. a brand new SSO user with zero company
		// memberships at all, so CompanySwitcher never has anything to pick) - nothing
		// to fetch, but still clear the spinner rather than spinning forever. Same guard
		// as agents/+page.svelte's load().
		if (!$tenantId) {
			workflows = [];
			loading = false;
			return;
		}
		try {
			workflows = await listWorkflowsForTenant($tenantId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load workflows.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$tenantId;
		load();
	});

	$effect(() => {
		headerContent.set({ menu: 'Workflows', title: 'Workflows', actions: newWorkflowAction });
	});

	let filtered = $derived(workflows.filter((w) => showArchived || w.status !== 'archived'));

	// A pooled workflow can belong to any company under the tenant, not just whichever one
	// $companyId currently points at - every other page (detail/edit/versions) still scopes
	// strictly by $companyId, so it must be pointed at this workflow's own company before
	// navigating into it, or the detail page 404s. Same approach as agents/+page.svelte's
	// openAgent().
	function openWorkflow(workflow: Workflow) {
		companyId.set(workflow.company_id);
	}

	function companyLabel(workflowCompanyId: string): string | undefined {
		return companyLabelForCompany($currentUser, workflowCompanyId);
	}
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
			No workflows yet for this tenant — a workflow orchestrates multiple published Agents
			through a graph of states (PRD §6.9).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Company</th>
					<th class="px-4 py-2">Status</th>
					<th class="px-4 py-2">Updated</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each filtered as workflow (workflow.id)}
					<tr>
						<td class="px-4 py-2 font-medium text-neutral-900">
							<a
								href="/workflows/{workflow.id}"
								onclick={() => openWorkflow(workflow)}
								class="text-primary underline"
							>
								{workflow.name}
							</a>
						</td>
						<td class="px-4 py-2 text-neutral-600">
							{#if companyLabel(workflow.company_id)}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
									{companyLabel(workflow.company_id)}
								</span>
							{/if}
						</td>
						<td class="px-4 py-2 text-neutral-600">{workflow.status}</td>
						<td class="px-4 py-2 text-neutral-600">{new Date(workflow.updated_at).toLocaleString()}</td>
						<td class="px-4 py-2 text-right">
							<a
								href="/workflows/{workflow.id}"
								onclick={() => openWorkflow(workflow)}
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
