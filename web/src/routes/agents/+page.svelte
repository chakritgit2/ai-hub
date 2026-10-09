<script lang="ts">
	import { onMount } from 'svelte';
	import { listAgents } from '$lib/api/console';
	import type { Agent } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	let agents = $state<Agent[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let search = $state('');
	let showArchived = $state(false);

	async function load() {
		loading = true;
		error = null;
		try {
			agents = await listAgents();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load agents.';
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
		headerContent.set({ menu: 'Agents', title: 'Agents catalog', actions: newAgentAction });
	});

	let filtered = $derived(
		agents
			.filter((a) => showArchived || a.status !== 'archived')
			.filter((a) => {
				const q = search.trim().toLowerCase();
				if (!q) return true;
				return [a.display_name, a.name, a.role, a.owner].some((v) =>
					v?.toLowerCase().includes(q)
				);
			})
	);
</script>

{#snippet newAgentAction()}
	<a
		href="/agents/new"
		class="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-white
			shadow-sm hover:bg-primary-600"
	>
		+ New agent
	</a>
{/snippet}

<div class="mb-4 flex flex-wrap items-center gap-4">
	<label class="block max-w-sm flex-1">
		<span class="sr-only">Search agents</span>
		<input
			type="search"
			placeholder="Search by name, role or owner…"
			bind:value={search}
			class="w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm shadow-sm
				focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
		/>
	</label>
	<label class="flex items-center gap-2 text-sm text-neutral-600">
		<input type="checkbox" bind:checked={showArchived} />
		Show archived
	</label>
</div>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading agents…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load agents</p>
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
			{agents.length === 0 ? 'No agents yet for this company.' : 'No agents match your search.'}
		</p>
	</div>
{:else}
	<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
		{#each filtered as agent (agent.id)}
			<a
				href="/agents/{agent.id}"
				class="block rounded-lg border border-neutral-200 bg-white p-4 shadow-sm transition-shadow
					hover:shadow-md"
			>
				<p class="font-medium text-neutral-900">{agent.display_name ?? agent.name}</p>
				<p class="mt-0.5 font-mono text-xs text-neutral-500">{agent.name}</p>
				{#if agent.role}
					<p class="mt-2 text-sm text-neutral-600">{agent.role}</p>
				{/if}
				<div class="mt-3 flex items-center justify-between text-xs text-neutral-500">
					<span>Owner: {agent.owner ?? '—'}</span>
					<span
						class="rounded-full px-2 py-0.5 font-medium
							{agent.status === 'active' ? 'bg-primary/10 text-primary-700' : 'bg-neutral-100 text-neutral-500'}"
					>
						{agent.status}
					</span>
				</div>
			</a>
		{/each}

		<a
			href="/agents/new"
			class="flex flex-col items-center justify-center rounded-lg border-2 border-dashed
				border-neutral-300 bg-white/50 p-4 text-center text-neutral-500 hover:border-primary
				hover:text-primary"
		>
			<span class="text-2xl leading-none">+</span>
			<span class="mt-1 text-sm">New agent — start from Identity</span>
		</a>
	</div>
{/if}
