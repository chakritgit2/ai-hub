<script lang="ts">
	import type { PageData } from './$types';
	import { headerContent } from '$lib/stores/header';

	let { data }: { data: PageData } = $props();

	type TabId =
		| 'identity'
		| 'model'
		| 'tools'
		| 'knowledge'
		| 'skills'
		| 'memory'
		| 'guardrails'
		| 'advanced';

	interface Tab {
		id: TabId;
		label: string;
		enabled: boolean;
		note?: string;
	}

	const tabs: Tab[] = [
		{ id: 'identity', label: 'Identity', enabled: true },
		{ id: 'model', label: 'Model', enabled: false, note: 'PRD §6.1 — connection, model, temperature, fallback' },
		{ id: 'tools', label: 'Tools', enabled: false, note: 'Phase 2 — PRD §6.5' },
		{ id: 'knowledge', label: 'Knowledge', enabled: false, note: 'Phase 2 — PRD §6.6' },
		{ id: 'skills', label: 'Skills', enabled: false, note: 'Phase 2 — PRD §6.6a' },
		{ id: 'memory', label: 'Memory', enabled: false, note: 'PRD §6.2' },
		{ id: 'guardrails', label: 'Guardrails', enabled: false, note: 'PRD §6.4' },
		{ id: 'advanced', label: 'Advanced', enabled: false, note: 'Read-only compiled_definition — PRD §6.1' }
	];

	let activeTab = $state<TabId>('identity');

	$effect(() => {
		headerContent.set({
			menu: 'Agents',
			title: data.agent?.display_name ?? data.agent?.name ?? 'Agent',
			actions: headerActions
		});
	});

	let identity = $derived(data.agent as unknown as Record<string, unknown> | null);
</script>

{#snippet headerActions()}
	<div class="flex gap-2">
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50"
		>
			Versions
		</button>
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50"
		>
			Save draft
		</button>
		<button
			type="button"
			class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm
				hover:bg-primary-600"
		>
			Publish
		</button>
	</div>
{/snippet}

{#if data.loadError}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this agent</p>
		<p class="mt-1 text-sm text-neutral-700">{data.loadError}</p>
	</div>
{:else if !data.agent}
	<p class="text-sm text-neutral-500">Agent not found.</p>
{:else}
	<div class="grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr]">
		<nav aria-label="Agent editor tabs" class="flex flex-row gap-1 overflow-x-auto lg:flex-col">
			{#each tabs as tab (tab.id)}
				<button
					type="button"
					class="rounded-md px-3 py-2 text-left text-sm whitespace-nowrap
						{activeTab === tab.id ? 'bg-primary/10 font-medium text-primary-700' : 'text-neutral-600 hover:bg-neutral-100'}
						{!tab.enabled ? 'opacity-60' : ''}"
					aria-current={activeTab === tab.id ? 'page' : undefined}
					aria-disabled={!tab.enabled}
					onclick={() => (activeTab = tab.id)}
				>
					{tab.label}
					{#if !tab.enabled}
						<span class="ml-1 text-xs text-neutral-400">(phase)</span>
					{/if}
				</button>
			{/each}

			<div class="mt-4 rounded-md border border-neutral-200 bg-neutral-50 p-3 text-xs text-neutral-500">
				<p>Versions: {data.versions.length}</p>
				<p class="mt-1">
					Status:
					<span class="font-medium text-neutral-700">{data.agent.status}</span>
				</p>
			</div>
		</nav>

		<div class="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
			{#if activeTab === 'identity'}
				<dl class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<div>
						<dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">Display name</dt>
						<dd class="mt-1 text-sm text-neutral-900">{data.agent.display_name ?? data.agent.name}</dd>
					</div>
					<div>
						<dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">Name (slug)</dt>
						<dd class="mt-1 font-mono text-sm text-neutral-900">{data.agent.name}</dd>
					</div>
					<div>
						<dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">Owner</dt>
						<dd class="mt-1 text-sm text-neutral-900">{data.agent.owner ?? '—'}</dd>
					</div>
					<div>
						<dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">Languages</dt>
						<dd class="mt-1 text-sm text-neutral-900">{(data.agent.languages ?? []).join(', ') || '—'}</dd>
					</div>
					<div class="sm:col-span-2">
						<dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">Role</dt>
						<dd class="mt-1 text-sm text-neutral-900">{data.agent.role ?? data.agent.description ?? '—'}</dd>
					</div>
				</dl>
				<p class="mt-6 text-xs text-neutral-500">
					Loaded via <code>getAgent</code> and <code>listAgentVersions</code> — the full editable
					form lives on <a href="/agents/new" class="text-primary underline">Agents → New</a> for now.
				</p>
			{:else}
				{@const tab = tabs.find((t) => t.id === activeTab)}
				<div class="rounded-md border border-dashed border-neutral-300 p-8 text-center">
					<p class="text-sm font-medium text-neutral-600">{tab?.label} is not part of this skeleton yet</p>
					{#if tab?.note}
						<p class="mt-1 text-sm text-neutral-400">{tab.note}</p>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/if}
