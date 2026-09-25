<script lang="ts">
	import { onMount } from 'svelte';
	import { listDeployments } from '$lib/api/console';
	import type { Deployment } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	headerContent.set({ menu: 'Deployments', title: 'Deployments' });

	let deployments = $state<Deployment[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			deployments = await listDeployments();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load deployments.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$companyId;
		load();
	});
</script>

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
		<p class="text-sm text-neutral-500">No deployments yet for this company.</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Slug</th>
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
							<a href="/deployments/{deployment.id}" class="font-mono text-primary underline">
								{deployment.slug}
							</a>
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
