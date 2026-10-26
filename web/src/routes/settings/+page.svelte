<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listCompanyUsers,
		listEgressAllowlist,
		createEgressAllowlistEntry,
		deleteEgressAllowlistEntry,
		listCompanies
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { Placeholder, Company, EgressAllowlistEntry } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { currentUser, roleForCompany } from '$lib/stores/auth';
	import { companyId } from '$lib/stores/company';

	// Egress allowlist is a per-company list (RLS-scoped, stamped with the caller's own
	// company_id on create) administered by that company's admins - requireAdminRole()
	// server-side, same as Connections, NOT platform_admin like the Companies/Pricing
	// tabs below.
	let isAdmin = $derived(roleForCompany($currentUser, $companyId) === 'admin');

	headerContent.set({ menu: 'Settings', title: 'Settings' });

	type TabId = 'users' | 'egress' | 'companies' | 'pricing';

	let activeTab = $state<TabId>('users');

	let users = $state<Placeholder[]>([]);
	let usersLoading = $state(true);
	let usersError = $state<string | null>(null);

	let egress = $state<EgressAllowlistEntry[]>([]);
	let egressLoading = $state(true);
	let egressError = $state<string | null>(null);

	let showEgressForm = $state(false);
	// port is bound to a type="number" input, which Svelte coerces to number | null
	// (never a string, even for the initial value) - typing it as a number here avoids
	// calling string-only methods on it later.
	let egressForm = $state<{ host_pattern: string; port: number | null; allow_private_ip: boolean }>({
		host_pattern: '',
		port: null,
		allow_private_ip: false
	});
	let egressFormError = $state<string | null>(null);
	let submittingEgress = $state(false);
	let deletingEgressId = $state<string | null>(null);
	let egressDeleteError = $state<string | null>(null);

	const EGRESS_ERROR_MESSAGES: Record<string, string> = {
		host_pattern_required: 'Host pattern is required.',
		invalid_port: 'Port must be a whole number between 1 and 65535.',
		invalid_allow_private_ip: 'Allow private IP must be a yes/no value.',
		forbidden: 'You no longer have permission to do this.'
	};

	function describeEgressError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) {
			return EGRESS_ERROR_MESSAGES[e.message] ?? e.message;
		}
		return e instanceof Error ? e.message : fallback;
	}

	let companies = $state<Company[]>([]);
	let companiesLoading = $state(true);
	let companiesError = $state<string | null>(null);

	async function loadUsers() {
		usersLoading = true;
		usersError = null;
		try {
			users = await listCompanyUsers();
		} catch (e) {
			usersError = e instanceof Error ? e.message : 'Failed to load users.';
		} finally {
			usersLoading = false;
		}
	}

	async function loadEgress() {
		egressLoading = true;
		egressError = null;
		try {
			egress = await listEgressAllowlist();
		} catch (e) {
			egressError = e instanceof Error ? e.message : 'Failed to load the egress allowlist.';
		} finally {
			egressLoading = false;
		}
	}

	async function submitEgressForm() {
		submittingEgress = true;
		egressFormError = null;
		try {
			await createEgressAllowlistEntry({
				host_pattern: egressForm.host_pattern.trim(),
				port: egressForm.port ?? undefined,
				allow_private_ip: egressForm.allow_private_ip
			});
			showEgressForm = false;
			egressForm = { host_pattern: '', port: null, allow_private_ip: false };
			await loadEgress();
		} catch (e) {
			egressFormError = describeEgressError(e, 'Failed to add this host.');
		} finally {
			submittingEgress = false;
		}
	}

	async function doDeleteEgress(entry: EgressAllowlistEntry) {
		if (!confirm(`Remove "${entry.host_pattern}" from the egress allowlist?`)) return;
		deletingEgressId = entry.id;
		egressDeleteError = null;
		try {
			await deleteEgressAllowlistEntry(entry.id);
			await loadEgress();
		} catch (e) {
			// not_found means it's already gone (deleted elsewhere) - just reload quietly
			// instead of surfacing an alarming error for something that's already resolved.
			if (e instanceof ApiError && e.message === 'not_found') {
				await loadEgress();
			} else {
				egressDeleteError = describeEgressError(e, 'Failed to remove this host.');
			}
		} finally {
			deletingEgressId = null;
		}
	}

	async function loadCompanies() {
		companiesLoading = true;
		companiesError = null;
		try {
			companies = await listCompanies();
		} catch (e) {
			companiesError = e instanceof Error ? e.message : 'Failed to load companies.';
		} finally {
			companiesLoading = false;
		}
	}

	onMount(() => {
		loadUsers();
		loadEgress();
	});

	// Same company_id-placeholder race every other list page handles (the initial fetch
	// can land before $companyId resolves to the real value) - re-fetch once it changes,
	// same pattern as datasets/+page.svelte's load().
	$effect(() => {
		$companyId;
		loadUsers();
		loadEgress();
	});

	$effect(() => {
		if (activeTab === 'companies' && $currentUser?.is_platform_admin && companies.length === 0) {
			loadCompanies();
		}
	});

	const roleDescriptions: Record<string, string> = {
		admin: 'Everything in the company, including Connections and code tools.',
		developer: 'Agents, HTTP Tools, KB, Eval, Playground.',
		viewer: 'Dashboard, Runs, Eval — read only.'
	};

	const tabs: { id: TabId; label: string; platformAdminOnly?: boolean }[] = [
		{ id: 'users', label: 'Users' },
		{ id: 'egress', label: 'Egress allowlist' },
		{ id: 'companies', label: 'Companies', platformAdminOnly: true },
		{ id: 'pricing', label: 'Model pricing', platformAdminOnly: true }
	];
</script>

<div class="flex gap-1 border-b border-neutral-200">
	{#each tabs as tab (tab.id)}
		{@const disabled = tab.platformAdminOnly && !$currentUser?.is_platform_admin}
		<button
			type="button"
			class="px-4 py-2 text-sm {activeTab === tab.id
				? 'border-b-2 border-primary font-medium text-primary-700'
				: 'text-neutral-500 hover:text-neutral-800'} {disabled ? 'cursor-not-allowed opacity-40' : ''}"
			aria-current={activeTab === tab.id ? 'page' : undefined}
			aria-disabled={disabled}
			disabled={disabled}
			title={disabled ? 'platform_admin only' : undefined}
			onclick={() => (activeTab = tab.id)}
		>
			{tab.label}
		</button>
	{/each}
</div>

<div class="mt-4">
	{#if activeTab === 'users'}
		<div class="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
			{#each Object.entries(roleDescriptions) as [role, desc] (role)}
				<div class="rounded-md border border-neutral-200 bg-white p-3 text-xs">
					<p class="font-medium capitalize text-neutral-800">{role}</p>
					<p class="mt-1 text-neutral-500">{desc}</p>
				</div>
			{/each}
		</div>

		{#if usersLoading}
			<p class="text-sm text-neutral-500" role="status">Loading users…</p>
		{:else if usersError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
				<p class="text-sm font-medium text-danger">Could not load users</p>
				<p class="mt-1 text-sm text-neutral-700">{usersError}</p>
				<button
					type="button"
					class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
					onclick={loadUsers}
				>
					Retry
				</button>
			</div>
		{:else if users.length === 0}
			<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
				<p class="text-sm text-neutral-500">No users synced from SSO yet.</p>
			</div>
		{:else}
			<pre class="rounded-lg border border-neutral-200 bg-white p-4 text-xs">{JSON.stringify(users, null, 2)}</pre>
		{/if}
	{:else if activeTab === 'egress'}
		<div class="mb-4 flex items-center justify-between">
			<p class="text-xs text-neutral-400">
				Hosts connections' API bases are allowed to reach (PRD §7.4) — checked again on every save.
			</p>
			{#if isAdmin}
				<button
					type="button"
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					onclick={() => (showEgressForm = !showEgressForm)}
				>
					+ Add host
				</button>
			{:else}
				<span class="text-sm text-neutral-400">Admin access required to manage the egress allowlist</span>
			{/if}
		</div>

		{#if showEgressForm}
			<form
				class="mb-4 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-3"
				onsubmit={(e) => {
					e.preventDefault();
					submitEgressForm();
				}}
			>
				{#if egressFormError}
					<div class="sm:col-span-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
						{egressFormError}
					</div>
				{/if}
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Host pattern</span>
					<input
						type="text"
						bind:value={egressForm.host_pattern}
						required
						placeholder="api.openai.com"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Port (optional)</span>
					<input
						type="number"
						min="1"
						max="65535"
						bind:value={egressForm.port}
						placeholder="443"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>
				<label class="flex items-end gap-2 pb-2">
					<input type="checkbox" bind:checked={egressForm.allow_private_ip} class="h-4 w-4" />
					<span class="text-sm text-neutral-700">Allow private IP</span>
				</label>
				<p class="text-xs text-neutral-400 sm:col-span-3">
					Only enable "Allow private IP" if this host genuinely resolves to an internal address you
					trust — it bypasses the usual SSRF protection for this entry.
				</p>
				<div class="flex gap-2 sm:col-span-3">
					<button
						type="submit"
						disabled={submittingEgress}
						class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					>
						{submittingEgress ? 'Saving…' : 'Add host'}
					</button>
					<button
						type="button"
						class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
						onclick={() => (showEgressForm = false)}
					>
						Cancel
					</button>
				</div>
			</form>
		{/if}

		{#if egressDeleteError}
			<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
				{egressDeleteError}
			</div>
		{/if}

		{#if egressLoading}
			<p class="text-sm text-neutral-500" role="status">Loading egress allowlist…</p>
		{:else if egressError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
				<p class="text-sm font-medium text-danger">Could not load the egress allowlist</p>
				<p class="mt-1 text-sm text-neutral-700">{egressError}</p>
				<button
					type="button"
					class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
					onclick={loadEgress}
				>
					Retry
				</button>
			</div>
		{:else if egress.length === 0}
			<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
				<p class="text-sm text-neutral-500">
					No egress allowlist entries yet — connections cannot reach any host until one is added
					(PRD §7.4).
				</p>
			</div>
		{:else}
			<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
				<table class="min-w-full divide-y divide-neutral-200 text-sm">
					<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
						<tr>
							<th class="px-4 py-2">Host pattern</th>
							<th class="px-4 py-2">Port</th>
							<th class="px-4 py-2">Allow private IP</th>
							<th class="px-4 py-2">Added</th>
							{#if isAdmin}
								<th class="px-4 py-2">Actions</th>
							{/if}
						</tr>
					</thead>
					<tbody class="divide-y divide-neutral-100">
						{#each egress as entry (entry.id)}
							<tr>
								<td class="px-4 py-2 font-mono text-xs text-neutral-800">{entry.host_pattern}</td>
								<td class="px-4 py-2 text-neutral-600">{entry.port ?? 'any'}</td>
								<td class="px-4 py-2 text-neutral-600">{entry.allow_private_ip ? 'Yes' : 'No'}</td>
								<td class="px-4 py-2 text-neutral-500">{new Date(entry.created_at).toLocaleString()}</td>
								{#if isAdmin}
									<td class="px-4 py-2">
										<button
											type="button"
											class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
											disabled={deletingEgressId === entry.id}
											onclick={() => doDeleteEgress(entry)}
										>
											{deletingEgressId === entry.id ? 'Removing…' : 'Delete'}
										</button>
									</td>
								{/if}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{:else if activeTab === 'companies'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else if companiesLoading}
			<p class="text-sm text-neutral-500" role="status">Loading companies…</p>
		{:else if companiesError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{companiesError}</div>
		{:else if companies.length === 0}
			<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
				<p class="text-sm text-neutral-500">No companies yet.</p>
			</div>
		{:else}
			<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
				<table class="min-w-full divide-y divide-neutral-200 text-sm">
					<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
						<tr>
							<th class="px-4 py-2">Code</th>
							<th class="px-4 py-2">Name</th>
							<th class="px-4 py-2">Status</th>
							<th class="px-4 py-2">Monthly budget</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-neutral-100">
						{#each companies as company (company.id)}
							<tr>
								<td class="px-4 py-2 font-mono text-xs">{company.code}</td>
								<td class="px-4 py-2 text-neutral-800">{company.name}</td>
								<td class="px-4 py-2">
									<span
										class="rounded-full px-2 py-0.5 text-xs font-medium
											{company.status === 'active' ? 'bg-primary/10 text-primary-700' : 'bg-danger/10 text-danger'}"
									>
										{company.status}
									</span>
								</td>
								<td class="px-4 py-2 text-neutral-600">
									{company.monthly_budget_usd != null ? `$${company.monthly_budget_usd}` : '—'}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<p class="mt-2 text-xs text-neutral-400">
				platform_admin sees only aggregate figures here — never secrets, knowledge or conversation
				content (PRD §7.2).
			</p>
		{/if}
	{:else if activeTab === 'pricing'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else}
			<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
				<p class="text-sm text-neutral-500">
					Model pricing (provider, model, input/output per 1K) has no dedicated endpoint in the
					current OpenAPI contract yet — add one under <code>/settings/model-pricing</code> when
					designed.
				</p>
			</div>
		{/if}
	{/if}
</div>
