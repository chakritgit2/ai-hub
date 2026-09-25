<script lang="ts">
	import { onMount } from 'svelte';
	import { listApiKeys, createApiKey } from '$lib/api/console';
	import type { ApiKey, ApiKeyCreated } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	let apiKeys = $state<ApiKey[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showForm = $state(false);

	let form = $state({ name: '', rate_limit_per_min: 60, daily_cost_limit_usd: 5, allowed_ips: '' });
	let formError = $state<string | null>(null);
	let submitting = $state(false);
	let justCreated = $state<ApiKeyCreated | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			apiKeys = await listApiKeys();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load API keys.';
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
		headerContent.set({ menu: 'API Keys', title: 'API Keys', actions: newKeyAction });
	});

	async function submitForm() {
		submitting = true;
		formError = null;
		try {
			justCreated = await createApiKey({
				name: form.name,
				rate_limit_per_min: form.rate_limit_per_min,
				daily_cost_limit_usd: form.daily_cost_limit_usd,
				allowed_ips: form.allowed_ips
					.split(',')
					.map((v) => v.trim())
					.filter(Boolean)
			});
			showForm = false;
			await load();
		} catch (e) {
			formError = e instanceof Error ? e.message : 'Failed to create the API key.';
		} finally {
			submitting = false;
		}
	}
</script>

{#snippet newKeyAction()}
	<button
		type="button"
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		onclick={() => (showForm = !showForm)}
	>
		+ New key
	</button>
{/snippet}

{#if justCreated}
	<div class="mb-6 rounded-md border border-warning/40 bg-warning/10 p-4">
		<p class="text-sm font-medium text-warning-dark">Shown once — copy this key now</p>
		<code class="mt-2 block break-all rounded bg-white px-3 py-2 font-mono text-sm text-neutral-800">
			{justCreated.key}
		</code>
		<button
			type="button"
			class="mt-3 rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
			onclick={() => (justCreated = null)}
		>
			Done
		</button>
	</div>
{/if}

{#if showForm}
	<form
		class="mb-6 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2"
		onsubmit={(e) => {
			e.preventDefault();
			submitForm();
		}}
	>
		{#if formError}
			<div class="sm:col-span-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
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
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Allowed IPs (comma-separated)</span>
			<input
				type="text"
				bind:value={form.allowed_ips}
				placeholder="203.0.113.4"
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Rate limit / min</span>
			<input
				type="number"
				min="1"
				bind:value={form.rate_limit_per_min}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Daily cost limit (USD)</span>
			<input
				type="number"
				min="0"
				step="0.01"
				bind:value={form.daily_cost_limit_usd}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<p class="text-xs text-neutral-500 sm:col-span-2">
			Scope this key to specific deployments of your own company after creating it — server-to-server
			only (browsers use 5-minute session tokens instead, PRD §6.3).
		</p>
		<div class="flex gap-2 sm:col-span-2">
			<button
				type="submit"
				disabled={submitting}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
			>
				{submitting ? 'Creating…' : 'Create key'}
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
	<p class="text-sm text-neutral-500" role="status">Loading API keys…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load API keys</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if apiKeys.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">No API keys yet for this company.</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Prefix</th>
					<th class="px-4 py-2">Allowed IPs</th>
					<th class="px-4 py-2">Last used</th>
					<th class="px-4 py-2">Status</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each apiKeys as key (key.id)}
					<tr class={key.revoked_at ? 'opacity-50' : ''}>
						<td class="px-4 py-2 font-medium text-neutral-900">{key.name}</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-600">{key.prefix}••••</td>
						<td class="px-4 py-2 text-neutral-600">{(key.allowed_ips ?? []).join(', ') || 'any'}</td>
						<td class="px-4 py-2 text-neutral-600">
							{key.last_used_at ? new Date(key.last_used_at).toLocaleString() : 'never'}
						</td>
						<td class="px-4 py-2">
							{#if key.revoked_at}
								<span class="text-xs font-medium text-neutral-500">revoked</span>
							{:else}
								<span class="text-xs font-medium text-primary-700">active</span>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
