<script lang="ts">
	import { onMount } from 'svelte';
	import { listToolsForTenant, createTool, updateTool, deleteTool, putToolSecret, testTool } from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { Tool } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId, tenantId } from '$lib/stores/company';
	import { currentUser, roleForCompany } from '$lib/stores/auth';

	// Pilot of "each menu shows only its own tenant's data" (option A) - pools tools
	// across every company under the selected tenant instead of the usual single
	// X-Company-Id company. See ToolsController::listTools()'s X-Tenant-Id branch and
	// agents/+page.svelte, the first menu this pattern was applied to.

	// Every mutator below is X-Company-Id-scoped via the shared companyId store
	// (api/client.ts), but a pooled tool can belong to any company under the tenant -
	// point companyId at that tool's own company before acting, same approach as
	// connections/+page.svelte's withCompany().
	function withCompany<T>(toolCompanyId: string, fn: () => Promise<T>): Promise<T> {
		companyId.set(toolCompanyId);
		return fn();
	}

	function companyLabel(toolCompanyId: string): string | undefined {
		return $currentUser?.companies.find((c) => c.company_id === toolCompanyId)?.company_name;
	}

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
		description: '',
		provider: 'tavily' as 'tavily' | 'exa'
	});
	let formError = $state<string | null>(null);
	let submitting = $state(false);
	let testResults = $state<Record<string, { ok: boolean; detail: string } | 'testing'>>({});

	let secretFormId = $state<string | null>(null);
	let secretValue = $state('');
	let secretError = $state<string | null>(null);
	let savingSecret = $state(false);

	let deletingId = $state<string | null>(null);
	let deleteError = $state<string | null>(null);

	let isAdmin = $derived(roleForCompany($currentUser, $companyId) === 'admin');

	// `kind: python` is code-execution and admin-only server-side (requireWriteRoleForKind)
	// - edit/delete must be hidden the same way the create form already disables selecting
	// it, instead of letting a developer click through to a silent/unexplained 403. A
	// pooled tool can belong to any company under the tenant, not just whichever one
	// $companyId currently points at, so this must be checked per-row against that tool's
	// own company (same reasoning as connections/+page.svelte's isAdminFor()).
	function canManage(tool: Tool): boolean {
		return tool.kind !== 'python' || roleForCompany($currentUser, tool.company_id) === 'admin';
	}

	// console-api's own error codes for create/update, mapped to readable copy -
	// egress_host_not_allowed carries the offending `host` in the body, folded in below.
	const ERROR_MESSAGES: Record<string, string> = {
		name_required: 'Name is required.',
		kind_required: 'Kind is required.',
		kind_invalid: 'Unsupported kind.',
		name_already_exists: 'A tool with this name already exists.',
		access_level_invalid: 'Unsupported access level.',
		auth_mode_invalid: 'Unsupported auth mode.',
		audience_must_be_string_or_null: 'Audience must be text.',
		config_url_must_be_string: 'URL must be text.',
		config_url_invalid: 'URL must be a valid absolute URL.',
		config_provider_invalid: 'Unsupported provider.',
		builtin_tools_require_service_auth_mode: 'Built-in tools always use service auth.',
		forbidden: 'You no longer have permission to do this.',
		not_found: 'This tool no longer exists - it may have been deleted by someone else.'
	};

	function describeError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) {
			if (e.message === 'egress_host_not_allowed') {
				const host = (e.body as { host?: string } | undefined)?.host;
				return `"${host ?? 'this host'}" is not on the egress allowlist - add it under Settings first.`;
			}
			return ERROR_MESSAGES[e.message] ?? e.message;
		}
		return e instanceof Error ? e.message : fallback;
	}

	async function load() {
		loading = true;
		error = null;
		// No tenant resolved yet (e.g. a brand new SSO user with zero company
		// memberships at all, so CompanySwitcher never has anything to pick) - nothing
		// to fetch, but still clear the spinner rather than spinning forever. Same guard
		// as agents/+page.svelte's load().
		if (!$tenantId) {
			tools = [];
			loading = false;
			return;
		}
		try {
			tools = await listToolsForTenant($tenantId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load tools.';
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
			description: '',
			provider: 'tavily'
		};
	}

	async function submitForm() {
		submitting = true;
		formError = null;
		try {
			const authMode = form.kind === 'builtin' ? 'service' : form.auth_mode;
			await createTool({
				name: form.name,
				kind: form.kind,
				access_level: form.access_level,
				auth_mode: authMode,
				audience: authMode === 'delegated' && form.audience ? form.audience : null,
				config:
					form.kind === 'http'
						? { url: form.url, method: form.method, description: form.description }
						: form.kind === 'builtin'
							? { provider: form.provider }
							: {}
			});
			showForm = false;
			resetForm();
			await load();
		} catch (e) {
			formError = describeError(e, 'Failed to create the tool.');
		} finally {
			submitting = false;
		}
	}

	let editFormId = $state<string | null>(null);
	// `kind` is intentionally not editable here - it decides config's whole shape
	// (url/method vs. provider vs. nothing), so a kind change is a delete+recreate, same
	// as how this page's create form never lets kind be changed after submit either.
	let editForm = $state({
		name: '',
		access_level: 'read' as Tool['access_level'],
		auth_mode: 'service' as Tool['auth_mode'],
		audience: '',
		url: '',
		method: 'GET',
		description: '',
		provider: 'tavily' as 'tavily' | 'exa',
		enabled: true
	});
	let editError = $state<string | null>(null);
	let savingEdit = $state(false);

	function openEditForm(tool: Tool) {
		editFormId = tool.id;
		editForm = {
			name: tool.name,
			access_level: tool.access_level,
			auth_mode: tool.auth_mode,
			audience: tool.audience ?? '',
			url: tool.kind === 'http' ? ((tool.config.url as string) ?? '') : '',
			method: tool.kind === 'http' ? ((tool.config.method as string) ?? 'GET') : 'GET',
			description: tool.kind === 'http' ? ((tool.config.description as string) ?? '') : '',
			provider: tool.kind === 'builtin' ? ((tool.config.provider as 'tavily' | 'exa') ?? 'tavily') : 'tavily',
			enabled: tool.enabled
		};
		editError = null;
	}

	async function submitEdit(tool: Tool) {
		savingEdit = true;
		editError = null;
		try {
			const authMode = tool.kind === 'builtin' ? 'service' : editForm.auth_mode;
			await withCompany(tool.company_id, () =>
				updateTool(tool.id, {
					name: editForm.name,
					access_level: editForm.access_level,
					auth_mode: authMode,
					audience: authMode === 'delegated' && editForm.audience ? editForm.audience : null,
					// PATCH overwrites `config` wholesale server-side (no merge) - for kinds this
					// form has no fields for (python), send the tool's existing config back
					// unchanged rather than {}, or saving just a rename/enabled-toggle would
					// silently wipe it.
					config:
						tool.kind === 'http'
							? { url: editForm.url, method: editForm.method, description: editForm.description }
							: tool.kind === 'builtin'
								? { provider: editForm.provider }
								: tool.config,
					enabled: editForm.enabled
				})
			);
			editFormId = null;
			await load();
		} catch (e) {
			editError = describeError(e, 'Failed to save changes.');
		} finally {
			savingEdit = false;
		}
	}

	async function remove(tool: Tool) {
		if (!confirm(`Delete tool "${tool.name}"? This cannot be undone.`)) return;
		deletingId = tool.id;
		deleteError = null;
		try {
			await withCompany(tool.company_id, () => deleteTool(tool.id));
			await load();
		} catch (e) {
			deleteError = describeError(e, 'Failed to delete this tool.');
		} finally {
			deletingId = null;
		}
	}

	function openSecretForm(tool: Tool) {
		secretFormId = tool.id;
		secretValue = '';
		secretError = null;
	}

	async function submitSecret(tool: Tool) {
		savingSecret = true;
		secretError = null;
		try {
			await withCompany(tool.company_id, () => putToolSecret(tool.id, { secret: secretValue }));
			secretFormId = null;
			secretValue = '';
			await load();
		} catch (e) {
			secretError = e instanceof Error ? e.message : 'Failed to save the secret.';
		} finally {
			savingSecret = false;
		}
	}

	async function runTest(tool: Tool) {
		testResults = { ...testResults, [tool.id]: 'testing' };
		try {
			const result = await withCompany(tool.company_id, () => testTool(tool.id));
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
		{#if form.kind !== 'builtin'}
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
		{/if}
		{#if form.kind !== 'builtin' && form.auth_mode === 'delegated'}
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
		{#if form.kind === 'builtin'}
			<label class="block">
				<span class="text-sm font-medium text-neutral-700">Provider</span>
				<select
					bind:value={form.provider}
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				>
					<option value="tavily">Tavily</option>
					<option value="exa">Exa</option>
				</select>
				<span class="mt-1 block text-xs text-neutral-400">
					Set the provider's API key via "Set secret" after saving.
				</span>
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

{#if deleteError}
	<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
		{deleteError}
	</div>
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
			No tools yet for this tenant — not yet callable by an agent (CRUD only, PRD §6.5).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Company</th>
					<th class="px-4 py-2">Kind</th>
					<th class="px-4 py-2">Access</th>
					<th class="px-4 py-2">Auth mode</th>
					<th class="px-4 py-2">Secret</th>
					<th class="px-4 py-2">URL / Provider</th>
					<th class="px-4 py-2">Enabled</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each tools as tool (tool.id)}
					<tr>
						<td class="px-4 py-2 font-medium text-neutral-900">{tool.name}</td>
						<td class="px-4 py-2 text-neutral-600">
							{#if companyLabel(tool.company_id)}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
									{companyLabel(tool.company_id)}
								</span>
							{/if}
						</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-600">{tool.kind}</td>
						<td class="px-4 py-2 text-neutral-600">{tool.access_level}</td>
						<td class="px-4 py-2 text-neutral-600">{tool.auth_mode}</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-500">
							{(tool.kind === 'http' || tool.kind === 'builtin') && tool.auth_mode === 'service'
								? (tool.masked_hint ?? '••••••••')
								: '—'}
						</td>
						<td class="px-4 py-2 text-neutral-600">
							{tool.kind === 'builtin'
								? `Provider: ${(tool.config.provider as string) ?? '—'}`
								: ((tool.config.url as string) ?? '—')}
						</td>
						<td class="px-4 py-2 text-neutral-600">{tool.enabled ? 'Yes' : 'No'}</td>
						<td class="px-4 py-2 text-right">
							<div class="flex items-center justify-end gap-2">
								{#if (tool.kind === 'http' || tool.kind === 'builtin') && tool.auth_mode === 'service'}
									<button
										type="button"
										class="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
										onclick={() => openSecretForm(tool)}
									>
										Set secret
									</button>
								{/if}
								{#if tool.kind === 'http' || tool.kind === 'builtin'}
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
								{#if canManage(tool)}
									<button
										type="button"
										class="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
										onclick={() => openEditForm(tool)}
									>
										Edit
									</button>
									<button
										type="button"
										class="rounded-md border border-danger/40 px-2 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
										disabled={deletingId === tool.id}
										onclick={() => remove(tool)}
									>
										{deletingId === tool.id ? 'Deleting…' : 'Delete'}
									</button>
								{:else}
									<span class="text-xs text-neutral-400">Admin access required</span>
								{/if}
							</div>
							{#if editFormId === tool.id}
								<form
									class="mt-2 grid grid-cols-1 gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-2 text-left sm:grid-cols-2"
									onsubmit={(e) => {
										e.preventDefault();
										submitEdit(tool);
									}}
								>
									{#if editError}
										<p class="text-xs text-danger sm:col-span-2">{editError}</p>
									{/if}
									<label class="block">
										<span class="text-xs font-medium text-neutral-700">Name</span>
										<input
											type="text"
											bind:value={editForm.name}
											required
											class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 text-xs
												focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
										/>
									</label>
									<label class="block">
										<span class="text-xs font-medium text-neutral-700">Access level</span>
										<select
											bind:value={editForm.access_level}
											class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 text-xs
												focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
										>
											<option value="read">Read</option>
											<option value="write">Write (requires human approval to execute)</option>
										</select>
									</label>
									{#if tool.kind !== 'builtin'}
										<label class="block">
											<span class="text-xs font-medium text-neutral-700">Auth mode</span>
											<select
												bind:value={editForm.auth_mode}
												class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 text-xs
													focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
											>
												<option value="service">Service (static token)</option>
												<option value="delegated">Delegated (end-user identity)</option>
											</select>
										</label>
									{/if}
									{#if tool.kind !== 'builtin' && editForm.auth_mode === 'delegated'}
										<label class="block">
											<span class="text-xs font-medium text-neutral-700">Audience</span>
											<input
												type="text"
												bind:value={editForm.audience}
												placeholder="the tool host, e.g. phalcon.internal"
												class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 text-xs
													focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
											/>
										</label>
									{/if}
									{#if tool.kind === 'http'}
										<label class="block">
											<span class="text-xs font-medium text-neutral-700">URL</span>
											<input
												type="text"
												bind:value={editForm.url}
												placeholder="https://phalcon.internal/api/orders"
												class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 text-xs
													focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
											/>
										</label>
										<label class="block">
											<span class="text-xs font-medium text-neutral-700">Method</span>
											<input
												type="text"
												bind:value={editForm.method}
												class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 font-mono text-xs
													focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
											/>
										</label>
										<label class="block sm:col-span-2">
											<span class="text-xs font-medium text-neutral-700">Description</span>
											<input
												type="text"
												bind:value={editForm.description}
												class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 text-xs
													focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
											/>
										</label>
									{/if}
									{#if tool.kind === 'builtin'}
										<label class="block">
											<span class="text-xs font-medium text-neutral-700">Provider</span>
											<select
												bind:value={editForm.provider}
												class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1 text-xs
													focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
											>
												<option value="tavily">Tavily</option>
												<option value="exa">Exa</option>
											</select>
										</label>
									{/if}
									<label class="flex items-center gap-2 text-xs text-neutral-700">
										<input type="checkbox" bind:checked={editForm.enabled} />
										Enabled
									</label>
									<div class="flex gap-2 sm:col-span-2">
										<button
											type="submit"
											disabled={savingEdit}
											class="rounded-md bg-primary px-3 py-1 text-xs font-medium text-white hover:bg-primary-600"
										>
											{savingEdit ? 'Saving…' : 'Save'}
										</button>
										<button
											type="button"
											class="rounded-md border border-neutral-300 px-3 py-1 text-xs text-neutral-700 hover:bg-white"
											onclick={() => (editFormId = null)}
										>
											Cancel
										</button>
									</div>
								</form>
							{/if}
							{#if secretFormId === tool.id}
								<form
									class="mt-2 flex flex-col gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-2 text-left"
									onsubmit={(e) => {
										e.preventDefault();
										submitSecret(tool);
									}}
								>
									{#if secretError}
										<p class="text-xs text-danger">{secretError}</p>
									{/if}
									<input
										type="password"
										bind:value={secretValue}
										required
										minlength="8"
										placeholder="New secret / API key (min. 8 characters)"
										autocomplete="off"
										class="w-full rounded-md border border-neutral-300 px-2 py-1 font-mono text-xs
											focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
									/>
									<div class="flex gap-2">
										<button
											type="submit"
											disabled={savingSecret}
											class="rounded-md bg-primary px-3 py-1 text-xs font-medium text-white hover:bg-primary-600"
										>
											{savingSecret ? 'Saving…' : 'Save'}
										</button>
										<button
											type="button"
											class="rounded-md border border-neutral-300 px-3 py-1 text-xs text-neutral-700 hover:bg-white"
											onclick={() => (secretFormId = null)}
										>
											Cancel
										</button>
									</div>
								</form>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
