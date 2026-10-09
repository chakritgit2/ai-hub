<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		createAgent,
		createAgentVersion,
		updateAgentVersion,
		publishAgentVersion,
		listConnections,
		listTools
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { AgentIdentity, Connection, ModelSpec, Tool } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';

	// Only connection types the compiler can actually build an LLM for today
	// (ai/app/integrations/dynamiq_adapter.py's _CONNECTION_BUILDERS/_LLM_TYPES) - anything
	// else fails at publish time with "Unsupported connection type", so there's no point
	// offering it here. Update this allowlist if the compiler gains another provider.
	const USABLE_CONNECTION_TYPES = new Set(['dynamiq.connections.OpenAI', 'openai']);

	// Only kind: "http" tools can actually be built into a real node today
	// (ai/app/services/compiler.py's _resolve_tools) - builtin/python references fail
	// compilation outright for every role, so there's no point offering them here.
	const USABLE_TOOL_KINDS = new Set(['http']);

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
		{ id: 'model', label: 'Model', enabled: true },
		{ id: 'tools', label: 'Tools', enabled: true },
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

	// temperature/max_tokens start as '' (empty = omit) but bind:value on a number input
	// resets a cleared field to `null`, not `''` (Svelte's own to_number() does this) - so
	// both must be treated as "unset", not just ''. Converted to a real ModelSpec only at
	// save time, so we never send temperature: NaN or max_tokens: 0 for a field the user
	// left (or cleared back to) blank.
	let model = $state<{ connection_id: string; model: string; temperature: string | number | null; max_tokens: string | number | null }>(
		{ connection_id: '', model: '', temperature: '', max_tokens: '' }
	);
	const isUnset = (value: string | number | null): boolean => value === '' || value === null;
	let connections = $state<Connection[]>([]);
	let connectionsError = $state<string | null>(null);
	let usableConnections = $derived(connections.filter((c) => USABLE_CONNECTION_TYPES.has(c.type)));

	let tools = $state<Tool[]>([]);
	let toolsError = $state<string | null>(null);
	let selectedToolIds = $state<string[]>([]);

	function toggleTool(toolId: string, checked: boolean) {
		selectedToolIds = checked
			? [...selectedToolIds, toolId]
			: selectedToolIds.filter((id) => id !== toolId);
	}

	// Set once Save draft's createAgent/createAgentVersion succeed - lets a retry after a
	// partial failure (e.g. agent created but version creation threw) skip re-creating
	// what already exists instead of leaving an orphaned agent row behind.
	let agentId = $state<string | null>(null);
	let versionId = $state<string | null>(null);
	let isPublished = $state(false);

	let saving = $state(false);
	let publishing = $state(false);
	let error = $state<string | null>(null);
	let publishErrors = $state<{ path: string; message: string }[]>([]);
	let savedNotice = $state(false);

	onMount(async () => {
		try {
			connections = await listConnections();
		} catch (e) {
			connectionsError = e instanceof Error ? e.message : 'Failed to load connections.';
		}
		try {
			tools = await listTools();
		} catch (e) {
			toolsError = e instanceof Error ? e.message : 'Failed to load tools.';
		}
	});

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
		error = null;
		publishErrors = [];
		savedNotice = false;
		identity.languages = languagesInput
			.split(',')
			.map((l) => l.trim())
			.filter(Boolean);
		identity.tags = tagsInput
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean);

		if (!model.connection_id || !model.model.trim()) {
			error = 'Model: please select a connection and enter a model name.';
			return;
		}
		if (!isUnset(model.max_tokens) && (!Number.isInteger(Number(model.max_tokens)) || Number(model.max_tokens) <= 0)) {
			error = 'Model: max tokens must be a whole number greater than 0.';
			return;
		}

		const modelSpec: ModelSpec = {
			connection_id: model.connection_id,
			model: model.model.trim(),
			...(!isUnset(model.temperature) ? { temperature: Number(model.temperature) } : {}),
			...(!isUnset(model.max_tokens) ? { max_tokens: Number(model.max_tokens) } : {})
		};
		const spec = {
			identity,
			model: modelSpec,
			tools: selectedToolIds.map((tool_id) => ({ tool_id }))
		};

		saving = true;
		try {
			if (!agentId) {
				const agent = await createAgent({
					name: identity.name,
					description: identity.role,
					status: 'draft'
				});
				agentId = agent.id;
			}

			if (!versionId) {
				const version = await createAgentVersion(agentId, { spec });
				versionId = version.id;
			} else {
				await updateAgentVersion(agentId, versionId, { spec });
			}
			savedNotice = true;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to save the draft agent.';
		} finally {
			saving = false;
		}
	}

	async function publish() {
		if (!agentId || !versionId || isPublished) return;
		error = null;
		publishErrors = [];
		savedNotice = false;
		publishing = true;
		try {
			await publishAgentVersion(agentId, versionId);
			isPublished = true;
			await goto(`/agents/${agentId}`);
		} catch (e) {
			if (e instanceof ApiError && e.status === 422 && e.body && typeof e.body === 'object' && 'errors' in e.body) {
				publishErrors = (e.body as { errors: { path: string; message: string }[] }).errors;
			} else {
				error = e instanceof Error ? e.message : 'Failed to publish.';
			}
		} finally {
			publishing = false;
		}
	}
</script>

{#snippet headerActions()}
	<div class="flex gap-2">
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
			onclick={saveDraft}
			disabled={saving || publishing || isPublished}
		>
			{saving ? 'Saving…' : versionId ? 'Save changes' : 'Save draft'}
		</button>
		<button
			type="button"
			class="rounded-md px-4 py-2 text-sm font-medium shadow-sm disabled:cursor-not-allowed
				{versionId && !isPublished ? 'bg-primary text-white hover:bg-primary-600' : 'bg-neutral-200 text-neutral-500'}"
			onclick={publish}
			disabled={!versionId || isPublished || saving || publishing}
			title={versionId ? undefined : 'Publish is available once the draft is saved'}
		>
			{publishing ? 'Publishing…' : isPublished ? 'Published' : 'Publish'}
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

		{#if publishErrors.length > 0}
			<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
				<p class="font-medium">Publish failed — compiling the spec reported these problems:</p>
				<ul class="mt-1 list-disc pl-5">
					{#each publishErrors as e (e.path)}
						<li><code>{e.path}</code>: {e.message}</li>
					{/each}
				</ul>
			</div>
		{/if}

		{#if savedNotice}
			<div class="mb-4 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-primary-700">
				Draft saved. You can keep editing, or Publish when ready.
			</div>
		{/if}

		{#if isPublished}
			<div class="mb-4 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-primary-700">
				Published. Redirecting…
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
		{:else if activeTab === 'model'}
			<form class="space-y-5" onsubmit={(e) => e.preventDefault()}>
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Connection</span>
					{#if connectionsError}
						<div class="mt-1 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
							{connectionsError}
						</div>
					{:else if connections.length > 0 && usableConnections.length === 0}
						<p class="mt-1 text-sm text-neutral-500">
							No OpenAI-compatible connections found.
							<a href="/connections" class="text-primary underline">Create a connection</a> first.
						</p>
					{:else}
						<select
							bind:value={model.connection_id}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						>
							<option value="" disabled>Select a connection</option>
							{#each usableConnections as connection (connection.id)}
								<option value={connection.id}>{connection.name}</option>
							{/each}
						</select>
					{/if}
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Model</span>
					<input
						type="text"
						bind:value={model.model}
						placeholder="gpt-4o-mini"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Temperature (optional)</span>
						<input
							type="number"
							step="0.1"
							min="0"
							max="2"
							bind:value={model.temperature}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Max tokens (optional)</span>
						<input
							type="number"
							step="1"
							min="1"
							bind:value={model.max_tokens}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
				</div>

				<p class="text-xs text-neutral-500">
					Only connections the compiler can build an LLM from today (OpenAI-compatible) are listed
					(PRD §6.1).
				</p>
			</form>
		{:else if activeTab === 'tools'}
			<div class="space-y-4">
				{#if toolsError}
					<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
						{toolsError}
					</div>
				{:else if tools.length === 0}
					<p class="text-sm text-neutral-500">
						No tools yet. <a href="/tools" class="text-primary underline">Create a tool</a> first.
					</p>
				{:else}
					<ul class="divide-y divide-neutral-200 rounded-md border border-neutral-200">
						{#each tools as tool (tool.id)}
							{@const usable = USABLE_TOOL_KINDS.has(tool.kind) && tool.enabled}
							<li class="flex items-start gap-3 p-3">
								<input
									type="checkbox"
									id={`tool-${tool.id}`}
									class="mt-1"
									disabled={!usable}
									checked={selectedToolIds.includes(tool.id)}
									onchange={(e) => toggleTool(tool.id, (e.currentTarget as HTMLInputElement).checked)}
								/>
								<label for={`tool-${tool.id}`} class="flex-1 {usable ? '' : 'opacity-60'}">
									<p class="text-sm font-medium text-neutral-900">{tool.name}</p>
									<p class="text-xs text-neutral-500">
										kind: <code>{tool.kind}</code>
										{#if !tool.enabled}· disabled{/if}
										{#if tool.kind !== 'http'}· not supported by the compiler yet{/if}
									</p>
								</label>
							</li>
						{/each}
					</ul>
				{/if}

				<p class="text-xs text-neutral-500">
					Only enabled <code>http</code> tools can be attached today (PRD §6.5) — other kinds
					fail compilation until tool-secret storage and an execution story exist for them.
					Manage tools on the <a href="/tools" class="text-primary underline">Tools</a> page.
				</p>
			</div>
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
