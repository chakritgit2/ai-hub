<script lang="ts">
	import { onMount } from 'svelte';
	import { listDeploymentsForTenant } from '$lib/api/console';
	import type { Deployment } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId, tenantId } from '$lib/stores/company';
	import { currentUser } from '$lib/stores/auth';

	// Pilot of "each menu shows only its own tenant's data" (option A) - pools deployments
	// across every company under the selected tenant instead of the usual single
	// X-Company-Id company. See DeploymentsController::listDeployments()'s X-Tenant-Id
	// branch and agents/+page.svelte, the first menu this pattern was applied to.

	let deployments = $state<Deployment[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		// No tenant resolved yet (e.g. a brand new SSO user with zero company
		// memberships at all, so CompanySwitcher never has anything to pick) - nothing
		// to fetch, but still clear the spinner rather than spinning forever. Same guard
		// as agents/+page.svelte's load().
		if (!$tenantId) {
			deployments = [];
			loading = false;
			return;
		}
		try {
			deployments = await listDeploymentsForTenant($tenantId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load deployments.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$tenantId;
		load();
	});

	// A pooled deployment can belong to any company under the tenant, not just whichever
	// one $companyId currently points at - every other page (detail/edit) still scopes
	// strictly by $companyId, so it must be pointed at this deployment's own company
	// before navigating into it, or the detail page 404s. Same approach as
	// agents/+page.svelte's openAgent().
	function openDeployment(deployment: Deployment) {
		companyId.set(deployment.company_id);
	}

	function companyLabel(deploymentCompanyId: string): string | undefined {
		return $currentUser?.companies.find((c) => c.company_id === deploymentCompanyId)?.company_name;
	}

	$effect(() => {
		headerContent.set({ menu: 'Deployments', title: 'Deployments', actions: newDeploymentAction });
	});
</script>

{#snippet newDeploymentAction()}
	<a
		href="/deployments/new"
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
	>
		+ New deployment
	</a>
{/snippet}

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading deployments…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load deployments</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if deployments.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">No deployments yet for this tenant.</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Slug</th>
					<th class="px-4 py-2">Company</th>
					<th class="px-4 py-2">Environment</th>
					<th class="px-4 py-2">Config version</th>
					<th class="px-4 py-2">Rate limit / min</th>
					<th class="px-4 py-2">Daily cost limit</th>
					<th class="px-4 py-2">Enabled</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each deployments as deployment (deployment.id)}
					<tr>
						<td class="px-4 py-2">
							<a
								href="/deployments/{deployment.id}"
								onclick={() => openDeployment(deployment)}
								class="font-mono text-primary underline"
							>
								{deployment.slug}
							</a>
						</td>
						<td class="px-4 py-2 text-neutral-600">
							{#if companyLabel(deployment.company_id)}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
									{companyLabel(deployment.company_id)}
								</span>
							{/if}
						</td>
						<td class="px-4 py-2 text-neutral-600">{deployment.environment}</td>
						<td class="px-4 py-2 text-neutral-600">{deployment.config_version ?? '—'}</td>
						<td class="px-4 py-2 text-neutral-600">{deployment.rate_limit_per_min ?? '—'}</td>
						<td class="px-4 py-2 text-neutral-600">
							{deployment.daily_cost_limit_usd != null ? `$${deployment.daily_cost_limit_usd}` : '—'}
						</td>
						<td class="px-4 py-2">
							<span
								class="rounded-full px-2 py-0.5 text-xs font-medium
									{deployment.enabled ? 'bg-primary/10 text-primary-700' : 'bg-neutral-100 text-neutral-500'}"
							>
								{deployment.enabled ? 'enabled' : 'disabled'}
							</span>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
