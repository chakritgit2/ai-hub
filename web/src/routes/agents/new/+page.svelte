<script lang="ts">
	import { goto } from '$app/navigation';
	import { createAgent } from '$lib/api/console';
	import type { AgentIdentity } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';

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
		/** Only Identity has a real form in this skeleton; the rest note their PRD phase/section. */
		enabled: boolean;
		note?: string;
	}

	// Order per PRD §6.1: Identity, Model, Tools, Knowledge, Skills, Memory, Guardrails, Advanced.
	const tabs: Tab[] = [
		{ id: 'identity', label: 'Identity', enabled: true },
		{ id: 'model', label: 'Model', enabled: false, note: 'PRD §6.1 — connection, model, temperature, fallback' },
		{ id: 'tools', label: 'Tools', enabled: false, note: 'Phase 2 — PRD §6.5' },
		{ id: 'knowledge', label: 'Knowledge', enabled: false, note: 'Phase 2 — PRD §6.6' },
		{ id: 'skills', label: 'Skills', enabled: false, note: 'Phase 2 — PRD §6.6a' },
		{ id: 'memory', label: 'Memory', enabled: false, note: 'PRD §6.2' },
		{ id: 'guardrails', label: 'Guardrails', enabled: false, note: 'PRD §6.4' },
		{ id: 'advanced', label: 'Advanced', enabled: false, note: 'Read-only compiled_definition, admin-only raw JSON — PRD §6.1' }
	];

	let activeTab = $state<TabId>('identity');

	let identity = $state<AgentIdentity>({
		display_name: '',
		name: '',
		owner: '',
		role: '',
		languages: ['th'],
		persona: '',
		tags: [],
		responsibilities: '',
		in_scope: '',
		out_of_scope: '',
		handoff: '',
		instructions: ''
	});

	let languagesInput = $state('th');
	let tagsInput = $state('');

	let saving = $state(false);
	let error = $state<string | null>(null);

	function slugify(value: string): string {
		return value
			.toLowerCase()
			.trim()
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/(^-|-$)/g, '');
	}

	function onDisplayNameInput(event: Event) {
		identity.display_name = (event.currentTarget as HTMLInputElement).value;
		if (!identity.name || identity.name === slugify(identity.display_name)) {
			identity.name = slugify(identity.display_name);
		}
	}

	$effect(() => {
		headerContent.set({ menu: 'Agents', title: 'New agent', actions: headerActions });
	});

	async function saveDraft() {
		saving = true;
		error = null;
		identity.languages = languagesInput
			.split(',')
			.map((l) => l.trim())
			.filter(Boolean);
		identity.tags = tagsInput
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean);

		try {
			const agent = await createAgent({
				name: identity.name,
				description: identity.role,
				status: 'draft'
			});
			await goto(`/agents/${agent.id}`);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to save the draft agent.';
		} finally {
			saving = false;
		}
	}
</script>

{#snippet headerActions()}
	<div class="flex gap-2">
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50"
			onclick={saveDraft}
			disabled={saving}
		>
			{saving ? 'Saving…' : 'Save draft'}
		</button>
		<button
			type="button"
			class="rounded-md bg-neutral-200 px-4 py-2 text-sm font-medium text-neutral-500"
			disabled
			title="Publish is available once the draft is saved"
		>
			Publish
		</button>
	</div>
{/snippet}

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
	</nav>

	<div class="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
		{#if error}
			<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
				{error}
			</div>
		{/if}

		{#if activeTab === 'identity'}
			<form class="space-y-5" onsubmit={(e) => e.preventDefault()}>
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Display name</span>
						<input
							type="text"
							value={identity.display_name}
							oninput={onDisplayNameInput}
							placeholder="Vending Helper"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Name (slug)</span>
						<input
							type="text"
							bind:value={identity.name}
							placeholder="vending-support"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Owner</span>
						<input
							type="text"
							bind:value={identity.owner}
							placeholder="CS team"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Languages (comma-separated)</span>
						<input
							type="text"
							bind:value={languagesInput}
							placeholder="th, en"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
				</div>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Role (one sentence)</span>
					<input
						type="text"
						bind:value={identity.role}
						placeholder="Answers customer questions about using machines and refunds"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Persona</span>
					<input
						type="text"
						bind:value={identity.persona}
						placeholder="Polite, concise"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Tags (comma-separated)</span>
					<input
						type="text"
						bind:value={tagsInput}
						placeholder="support, vending"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Responsibilities</span>
					<textarea
						rows="3"
						bind:value={identity.responsibilities}
						placeholder="Check order status, explain refund steps, recommend products"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					></textarea>
				</label>

				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">In scope</span>
						<textarea
							rows="3"
							bind:value={identity.in_scope}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						></textarea>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Out of scope</span>
						<textarea
							rows="3"
							bind:value={identity.out_of_scope}
							placeholder="No topics outside the vending business; never grants discounts"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						></textarea>
					</label>
				</div>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Handoff (condition → channel)</span>
					<input
						type="text"
						bind:value={identity.handoff}
						placeholder="Refund above 500 THB → call center"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Instructions (Markdown, optional)</span>
					<textarea
						rows="4"
						bind:value={identity.instructions}
						placeholder="Procedures, sample replies…"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					></textarea>
				</label>

				<p class="text-xs text-neutral-500">
					The compiler assembles this identity into the agent's system prompt from a shared
					template, including instructions to decline <code>out_of_scope</code> requests and follow
					<code>handoff</code> (PRD §6.1a).
				</p>
			</form>
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
