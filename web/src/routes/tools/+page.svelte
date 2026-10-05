<script lang="ts">
	import { onMount } from 'svelte';
	import { listTools, createTool, deleteTool, testTool } from '$lib/api/console';
	import type { Tool } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';
	import { currentUser, roleForCompany } from '$lib/stores/auth';

	let tools = $state<Tool[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showForm = $state(false);

	let form = $state({
		name: '',
		kind: 'http' as Tool['kind'],
		access_level: 'read' as Tool['access_level'],
		auth_mode: 'service' as Tool['auth_mode'],
		audience: '',
		url: '',
		method: 'GET',
		description: ''
	});
	let formError = $state<string | null>(null);
	let submitting = $state(false);
	let testResults = $state<Record<string, { ok: boolean; detail: string } | 'testing'>>({});

	let isAdmin = $derived(roleForCompany($currentUser, $companyId) === 'admin');

	async function load() {
		loading = true;
		error = null;
		try {
			tools = await listTools();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load tools.';
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
		headerContent.set({ menu: 'Tools', title: 'Tools', actions: newToolAction });
	});

	function resetForm() {
		form = {
			name: '',
			kind: 'http',
			access_level: 'read',
			auth_mode: 'service',
			audience: '',
			url: '',
			method: 'GET',
			description: ''
		};
	}

	async function submitForm() {
		submitting = true;
		formError = null;
		try {
			await createTool({
				name: form.name,
				kind: form.kind,
				access_level: form.access_level,
				auth_mode: form.auth_mode,
				audience: form.auth_mode === 'delegated' && form.audience ? form.audience : null,
				config:
					form.kind === 'http'
						? { url: form.url, method: form.method, description: form.description }
						: {}
			});
			showForm = false;
			resetForm();
			await load();
		} catch (e) {
			formError = e instanceof Error ? e.message : 'Failed to create the tool.';
		} finally {
			submitting = false;
		}
	}

	async function remove(tool: Tool) {
		await deleteTool(tool.id);
		await load();
	}

	async function runTest(tool: Tool) {
		testResults = { ...testResults, [tool.id]: 'testing' };
		try {
			const result = await testTool(tool.id);
			testResults = { ...testResults, [tool.id]: result };
		} catch (e) {
			testResults = {
				...testResults,
				[tool.id]: { ok: false, detail: e instanceof Error ? e.message : 'Test request failed.' }
			};
		}
	}
</script>

{#snippet newToolAction()}
	<button
		type="button"
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		onclick={() => (showForm = !showForm)}
	>
		+ New tool
	</button>
{/snippet}

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
			<span class="text-sm font-medium text-neutral-700">Kind</span>
			<select
				bind:value={form.kind}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			>
				<option value="http">HTTP</option>
				<option value="builtin">Built-in (Tavily/Exa)</option>
				<option value="python" disabled={!isAdmin}>
					Python (code execution — admin only)
				</option>
			</select>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Access level</span>
			<select
				bind:value={form.access_level}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			>
				<option value="read">Read</option>
				<option value="write">Write (requires human approval to execute)</option>
			</select>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Auth mode</span>
			<select
				bind:value={form.auth_mode}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			>
				<option value="service">Service (static token)</option>
				<option value="delegated">Delegated (end-user identity)</option>
			</select>
		</label>
		{#if form.auth_mode === 'delegated'}
			<label class="block">
				<span class="text-sm font-medium text-neutral-700">Audience</span>
				<input
					type="text"
					bind:value={form.audience}
					placeholder="the tool host, e.g. phalcon.internal"
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				/>
			</label>
		{/if}
		{#if form.kind === 'http'}
			<label class="block">
				<span class="text-sm font-medium text-neutral-700">URL</span>
				<input
					type="text"
					bind:value={form.url}
					placeholder="https://phalcon.internal/api/orders"
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				/>
				<span class="mt-1 block text-xs text-neutral-400">
					Checked against the egress allowlist on save (PRD §7.4).
				</span>
			</label>
			<label class="block">
				<span class="text-sm font-medium text-neutral-700">Method</span>
				<input
					type="text"
					bind:value={form.method}
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				/>
			</label>
			<label class="block sm:col-span-2">
				<span class="text-sm font-medium text-neutral-700">Description</span>
				<input
					type="text"
					bind:value={form.description}
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				/>
			</label>
		{/if}
		<div class="flex gap-2 sm:col-span-2">
			<button
				type="submit"
				disabled={submitting}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
			>
				{submitting ? 'Saving…' : 'Save tool'}
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
	<p class="text-sm text-neutral-500" role="status">Loading tools…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load tools</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if tools.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">
			No tools yet for this company — not yet callable by an agent (CRUD only, PRD §6.5).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Kind</th>
					<th class="px-4 py-2">Access</th>
					<th class="px-4 py-2">Auth mode</th>
					<th class="px-4 py-2">URL</th>
					<th class="px-4 py-2">Enabled</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each tools as tool (tool.id)}
					<tr>
						<td class="px-4 py-2 font-medium text-neutral-900">{tool.name}</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-600">{tool.kind}</td>
						<td class="px-4 py-2 text-neutral-600">{tool.access_level}</td>
						<td class="px-4 py-2 text-neutral-600">{tool.auth_mode}</td>
						<td class="px-4 py-2 text-neutral-600">{(tool.config.url as string) ?? '—'}</td>
						<td class="px-4 py-2 text-neutral-600">{tool.enabled ? 'Yes' : 'No'}</td>
						<td class="px-4 py-2 text-right">
							<div class="flex items-center justify-end gap-2">
								{#if tool.kind === 'http'}
									{#if testResults[tool.id] === 'testing'}
										<span class="text-xs text-neutral-400">Testing…</span>
									{:else if testResults[tool.id]}
										{@const result = testResults[tool.id]}
										{#if typeof result === 'object'}
											<span
												class="text-xs {result.ok ? 'text-primary' : 'text-danger'}"
												title={result.detail}
											>
												{result.ok ? 'OK' : 'Failed'}
											</span>
										{/if}
									{/if}
									<button
										type="button"
										class="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
										onclick={() => runTest(tool)}
									>
										Test
									</button>
								{/if}
								<button
									type="button"
									class="rounded-md border border-danger/40 px-2 py-1 text-xs text-danger hover:bg-danger/10"
									onclick={() => remove(tool)}
								>
									Delete
								</button>
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
