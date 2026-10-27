<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listCompanyUsers,
		listEgressAllowlist,
		createEgressAllowlistEntry,
		deleteEgressAllowlistEntry,
		listCompanies,
		createCompany,
		suspendCompany,
		activateCompany,
		destroyCompanyDek,
		listTenants,
		createTenant,
		suspendTenant,
		activateTenant,
		listAuditLogs
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { CompanyUser, Company, Tenant, EgressAllowlistEntry, AuditLogEntry } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { currentUser, roleForCompany } from '$lib/stores/auth';
	import { companyId } from '$lib/stores/company';

	// Egress allowlist is a per-company list (RLS-scoped, stamped with the caller's own
	// company_id on create) administered by that company's admins - requireAdminRole()
	// server-side, same as Connections, NOT platform_admin like the Companies/Pricing
	// tabs below.
	let isAdmin = $derived(roleForCompany($currentUser, $companyId) === 'admin');

	headerContent.set({ menu: 'Settings', title: 'Settings' });

	type TabId = 'users' | 'egress' | 'tenants' | 'companies' | 'pricing' | 'audit';

	let activeTab = $state<TabId>('users');

	let users = $state<CompanyUser[]>([]);
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

	let tenants = $state<Tenant[]>([]);
	let tenantsLoading = $state(true);
	let tenantsError = $state<string | null>(null);

	function tenantName(tenantId: string): string {
		return tenants.find((t) => t.id === tenantId)?.name ?? tenantId;
	}

	const AUDIT_LOG_PAGE_SIZE = 50;

	let auditLogs = $state<AuditLogEntry[]>([]);
	let auditLoading = $state(true);
	let auditError = $state<string | null>(null);
	let auditHasMore = $state(false);
	let auditOffset = $state(0);
	let auditFrom = $state('');
	let auditTo = $state('');
	let auditExpandedId = $state<string | null>(null);

	const AUDIT_LOG_ERROR_MESSAGES: Record<string, string> = {
		invalid_from: 'The "From" date is not valid.',
		invalid_to: 'The "To" date is not valid.',
		invalid_limit: 'The page size is not valid.',
		invalid_offset: 'The page offset is not valid.',
		forbidden: 'You no longer have permission to view audit logs.'
	};

	function describeAuditError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) {
			return AUDIT_LOG_ERROR_MESSAGES[e.message] ?? e.message;
		}
		return e instanceof Error ? e.message : fallback;
	}

	async function loadAuditLogs(reset: boolean) {
		const offset = reset ? 0 : auditOffset;
		auditLoading = true;
		auditError = null;
		try {
			const response = await listAuditLogs({
				from: auditFrom || undefined,
				to: auditTo || undefined,
				limit: AUDIT_LOG_PAGE_SIZE,
				offset
			});
			auditLogs = reset ? response.items : [...auditLogs, ...response.items];
			auditHasMore = response.has_more;
			auditOffset = offset + response.items.length;
		} catch (e) {
			auditError = describeAuditError(e, 'Failed to load audit logs.');
		} finally {
			auditLoading = false;
		}
	}

	function clearAuditFilter() {
		auditFrom = '';
		auditTo = '';
		loadAuditLogs(true);
	}

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

	async function loadTenants() {
		tenantsLoading = true;
		tenantsError = null;
		try {
			tenants = await listTenants();
		} catch (e) {
			tenantsError = e instanceof Error ? e.message : 'Failed to load tenants.';
		} finally {
			tenantsLoading = false;
		}
	}

	const TENANT_ERROR_MESSAGES: Record<string, string> = {
		code_required: 'Code is required.',
		name_required: 'Name is required.',
		code_already_exists: 'A tenant with this code already exists.',
		forbidden: 'You no longer have permission to do this.',
		not_found: 'This tenant no longer exists - it may have been changed by someone else.'
	};

	function describeTenantError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) return TENANT_ERROR_MESSAGES[e.message] ?? e.message;
		return e instanceof Error ? e.message : fallback;
	}

	let showTenantForm = $state(false);
	let tenantForm = $state({ code: '', name: '' });
	let tenantFormError = $state<string | null>(null);
	let submittingTenant = $state(false);
	let tenantActionId = $state<string | null>(null);
	let tenantActionError = $state<string | null>(null);

	async function submitTenantForm() {
		submittingTenant = true;
		tenantFormError = null;
		try {
			await createTenant({ code: tenantForm.code.trim(), name: tenantForm.name.trim() });
			showTenantForm = false;
			tenantForm = { code: '', name: '' };
			await loadTenants();
		} catch (e) {
			tenantFormError = describeTenantError(e, 'Failed to create this tenant.');
		} finally {
			submittingTenant = false;
		}
	}

	async function doSuspendTenant(tenant: Tenant) {
		tenantActionId = tenant.id;
		tenantActionError = null;
		try {
			await suspendTenant(tenant.id);
			await loadTenants();
		} catch (e) {
			tenantActionError = describeTenantError(e, 'Failed to suspend this tenant.');
		} finally {
			tenantActionId = null;
		}
	}

	async function doActivateTenant(tenant: Tenant) {
		tenantActionId = tenant.id;
		tenantActionError = null;
		try {
			await activateTenant(tenant.id);
			await loadTenants();
		} catch (e) {
			tenantActionError = describeTenantError(e, 'Failed to activate this tenant.');
		} finally {
			tenantActionId = null;
		}
	}

	const COMPANY_ERROR_MESSAGES: Record<string, string> = {
		tenant_id_required: 'Tenant is required.',
		tenant_not_found: 'The selected tenant no longer exists.',
		code_required: 'Code is required.',
		name_required: 'Name is required.',
		code_already_exists: 'A company with this code already exists.',
		invalid_monthly_budget_usd: 'Monthly budget must be a number.',
		forbidden: 'You no longer have permission to do this.',
		not_found: 'This company no longer exists - it may have been changed by someone else.'
	};

	function describeCompanyError(e: unknown, fallback: string): string {
		if (e instanceof ApiError) return COMPANY_ERROR_MESSAGES[e.message] ?? e.message;
		return e instanceof Error ? e.message : fallback;
	}

	let showCompanyForm = $state(false);
	// monthly_budget_usd is bound to a type="number" input, which Svelte coerces to
	// number | null (same reasoning as egressForm.port above).
	let companyForm = $state<{
		tenant_id: string;
		code: string;
		name: string;
		external_ref: string;
		monthly_budget_usd: number | null;
	}>({ tenant_id: '', code: '', name: '', external_ref: '', monthly_budget_usd: null });
	let companyFormError = $state<string | null>(null);
	let submittingCompany = $state(false);
	let companyActionId = $state<string | null>(null);
	let companyActionError = $state<string | null>(null);
	let destroyingDekId = $state<string | null>(null);
	let dekDestroyedIds = $state<Set<string>>(new Set());

	async function submitCompanyForm() {
		submittingCompany = true;
		companyFormError = null;
		try {
			await createCompany({
				tenant_id: companyForm.tenant_id,
				code: companyForm.code.trim(),
				name: companyForm.name.trim(),
				external_ref: companyForm.external_ref.trim() === '' ? undefined : companyForm.external_ref.trim(),
				monthly_budget_usd: companyForm.monthly_budget_usd ?? undefined
			});
			showCompanyForm = false;
			companyForm = { tenant_id: '', code: '', name: '', external_ref: '', monthly_budget_usd: null };
			await loadCompanies();
		} catch (e) {
			companyFormError = describeCompanyError(e, 'Failed to create this company.');
		} finally {
			submittingCompany = false;
		}
	}

	async function doSuspendCompany(company: Company) {
		companyActionId = company.id;
		companyActionError = null;
		try {
			await suspendCompany(company.id);
			await loadCompanies();
		} catch (e) {
			companyActionError = describeCompanyError(e, 'Failed to suspend this company.');
		} finally {
			companyActionId = null;
		}
	}

	async function doActivateCompany(company: Company) {
		companyActionId = company.id;
		companyActionError = null;
		try {
			await activateCompany(company.id);
			await loadCompanies();
		} catch (e) {
			companyActionError = describeCompanyError(e, 'Failed to activate this company.');
		} finally {
			companyActionId = null;
		}
	}

	async function doDestroyDek(company: Company) {
		if (
			!confirm(
				`Permanently destroy the encryption key for "${company.name}"? Every connection secret in this company becomes undecryptable immediately. This cannot be undone.`
			)
		) {
			return;
		}
		destroyingDekId = company.id;
		companyActionError = null;
		try {
			await destroyCompanyDek(company.id);
			dekDestroyedIds = new Set([...dekDestroyedIds, company.id]);
		} catch (e) {
			companyActionError = describeCompanyError(e, "Failed to destroy this company's encryption key.");
		} finally {
			destroyingDekId = null;
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

	// Tenants are needed by both tabs - the Tenants tab itself, and the Companies tab's
	// create-form tenant picker + the tenant name shown per row.
	$effect(() => {
		if (
			(activeTab === 'tenants' || activeTab === 'companies') &&
			$currentUser?.is_platform_admin &&
			tenants.length === 0
		) {
			loadTenants();
		}
	});

	$effect(() => {
		if (activeTab === 'audit' && $currentUser?.is_platform_admin && auditLogs.length === 0) {
			loadAuditLogs(true);
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
		{ id: 'tenants', label: 'Tenants', platformAdminOnly: true },
		{ id: 'companies', label: 'Companies', platformAdminOnly: true },
		{ id: 'pricing', label: 'Model pricing', platformAdminOnly: true },
		{ id: 'audit', label: 'Audit Log', platformAdminOnly: true }
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
			<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
				<table class="min-w-full divide-y divide-neutral-200 text-sm">
					<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
						<tr>
							<th class="px-4 py-2">Email</th>
							<th class="px-4 py-2" title="This user's role within the current company.">Role</th>
							<th class="px-4 py-2" title="Platform admins can also manage Tenants, Companies and Audit Log.">
								Platform admin
							</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-neutral-100">
						{#each users as user (user.id)}
							<tr>
								<td class="px-4 py-2 text-neutral-800">{user.email}</td>
								<td class="px-4 py-2 capitalize text-neutral-600">{user.role}</td>
								<td class="px-4 py-2">
									{#if user.is_platform_admin}
										<span class="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary-700">
											Yes
										</span>
									{:else}
										<span class="text-neutral-400">—</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
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
	{:else if activeTab === 'tenants'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else}
			<div class="mb-4 flex items-center justify-between">
				<p class="text-xs text-neutral-400">
					The org-level grouping above Companies - most deployments only ever need one.
				</p>
				<button
					type="button"
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					onclick={() => (showTenantForm = !showTenantForm)}
				>
					+ New tenant
				</button>
			</div>

			{#if showTenantForm}
				<form
					class="mb-4 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2"
					onsubmit={(e) => {
						e.preventDefault();
						submitTenantForm();
					}}
				>
					{#if tenantFormError}
						<div class="sm:col-span-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
							{tenantFormError}
						</div>
					{/if}
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Code</span>
						<input
							type="text"
							bind:value={tenantForm.code}
							required
							placeholder="advws"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Name</span>
						<input
							type="text"
							bind:value={tenantForm.name}
							required
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<div class="flex gap-2 sm:col-span-2">
						<button
							type="submit"
							disabled={submittingTenant}
							class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						>
							{submittingTenant ? 'Saving…' : 'Save tenant'}
						</button>
						<button
							type="button"
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
							onclick={() => (showTenantForm = false)}
						>
							Cancel
						</button>
					</div>
				</form>
			{/if}

			{#if tenantActionError}
				<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
					{tenantActionError}
				</div>
			{/if}

			{#if tenantsLoading}
				<p class="text-sm text-neutral-500" role="status">Loading tenants…</p>
			{:else if tenantsError}
				<div class="rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{tenantsError}</div>
			{:else if tenants.length === 0}
				<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
					<p class="text-sm text-neutral-500">No tenants yet.</p>
				</div>
			{:else}
				<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
					<table class="min-w-full divide-y divide-neutral-200 text-sm">
						<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
							<tr>
								<th class="px-4 py-2">Code</th>
								<th class="px-4 py-2">Name</th>
								<th class="px-4 py-2">Status</th>
								<th class="px-4 py-2">Actions</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each tenants as tenant (tenant.id)}
								<tr>
									<td class="px-4 py-2 font-mono text-xs">{tenant.code}</td>
									<td class="px-4 py-2 text-neutral-800">{tenant.name}</td>
									<td class="px-4 py-2">
										<span
											class="rounded-full px-2 py-0.5 text-xs font-medium
												{tenant.status === 'active' ? 'bg-primary/10 text-primary-700' : 'bg-danger/10 text-danger'}"
										>
											{tenant.status}
										</span>
									</td>
									<td class="px-4 py-2">
										{#if tenant.status === 'active'}
											<button
												type="button"
												class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
												disabled={tenantActionId === tenant.id}
												onclick={() => doSuspendTenant(tenant)}
											>
												{tenantActionId === tenant.id ? 'Suspending…' : 'Suspend'}
											</button>
										{:else}
											<button
												type="button"
												class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
												disabled={tenantActionId === tenant.id}
												onclick={() => doActivateTenant(tenant)}
											>
												{tenantActionId === tenant.id ? 'Activating…' : 'Activate'}
											</button>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		{/if}
	{:else if activeTab === 'companies'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else}
			<div class="mb-4 flex items-center justify-between">
				<p class="text-xs text-neutral-400">
					platform_admin sees only aggregate figures here — never secrets, knowledge or conversation
					content (PRD §7.2).
				</p>
				{#if tenants.length > 0}
					<button
						type="button"
						class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						onclick={() => {
							if (!showCompanyForm) companyForm.tenant_id = tenants[0]?.id ?? '';
							showCompanyForm = !showCompanyForm;
						}}
					>
						+ New company
					</button>
				{:else}
					<span class="text-sm text-neutral-400">Create a tenant first, under the Tenants tab</span>
				{/if}
			</div>

			{#if showCompanyForm}
				<form
					class="mb-4 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2"
					onsubmit={(e) => {
						e.preventDefault();
						submitCompanyForm();
					}}
				>
					{#if companyFormError}
						<div class="sm:col-span-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
							{companyFormError}
						</div>
					{/if}
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Tenant</span>
						<select
							bind:value={companyForm.tenant_id}
							required
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						>
							{#each tenants as tenant (tenant.id)}
								<option value={tenant.id}>{tenant.name}</option>
							{/each}
						</select>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Code</span>
						<input
							type="text"
							bind:value={companyForm.code}
							required
							placeholder="operations"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Name</span>
						<input
							type="text"
							bind:value={companyForm.name}
							required
							placeholder="Operations"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">External ref (optional)</span>
						<input
							type="text"
							bind:value={companyForm.external_ref}
							placeholder="matches the SSO JWT's companies[].ref claim"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
						<span class="mt-1 block text-xs text-neutral-400">
							Users only get synced into this company on login if their SSO claim's ref matches this.
						</span>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Monthly budget USD (optional)</span>
						<input
							type="number"
							min="0"
							step="0.01"
							bind:value={companyForm.monthly_budget_usd}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<div class="flex gap-2 sm:col-span-2">
						<button
							type="submit"
							disabled={submittingCompany}
							class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
						>
							{submittingCompany ? 'Saving…' : 'Save company'}
						</button>
						<button
							type="button"
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
							onclick={() => (showCompanyForm = false)}
						>
							Cancel
						</button>
					</div>
				</form>
			{/if}

			{#if companyActionError}
				<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
					{companyActionError}
				</div>
			{/if}

			{#if companiesLoading}
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
								<th class="px-4 py-2">Tenant</th>
								<th class="px-4 py-2">Status</th>
								<th class="px-4 py-2">Monthly budget</th>
								<th class="px-4 py-2">Actions</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each companies as company (company.id)}
								<tr>
									<td class="px-4 py-2 font-mono text-xs">{company.code}</td>
									<td class="px-4 py-2 text-neutral-800">{company.name}</td>
									<td class="px-4 py-2 text-neutral-600">{tenantName(company.tenant_id)}</td>
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
									<td class="px-4 py-2">
										<div class="flex flex-wrap items-center gap-2">
											{#if company.status === 'active'}
												<button
													type="button"
													class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
													disabled={companyActionId === company.id}
													onclick={() => doSuspendCompany(company)}
												>
													{companyActionId === company.id ? 'Suspending…' : 'Suspend'}
												</button>
											{:else}
												<button
													type="button"
													class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
													disabled={companyActionId === company.id}
													onclick={() => doActivateCompany(company)}
												>
													{companyActionId === company.id ? 'Activating…' : 'Activate'}
												</button>
											{/if}
											{#if dekDestroyedIds.has(company.id)}
												<span class="text-xs text-danger">Key destroyed</span>
											{:else}
												<button
													type="button"
													class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
													disabled={destroyingDekId === company.id}
													onclick={() => doDestroyDek(company)}
												>
													{destroyingDekId === company.id ? 'Destroying…' : 'Destroy key'}
												</button>
											{/if}
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
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
	{:else if activeTab === 'audit'}
		{#if !$currentUser?.is_platform_admin}
			<div class="rounded-md border border-neutral-200 bg-white p-8 text-center text-sm text-neutral-500">
				platform_admin only.
			</div>
		{:else}
			<div class="mb-4 flex flex-wrap items-end gap-3">
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">From</span>
					<input
						type="date"
						bind:value={auditFrom}
						class="mt-1 rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">To</span>
					<input
						type="date"
						bind:value={auditTo}
						class="mt-1 rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>
				<button
					type="button"
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
					onclick={() => loadAuditLogs(true)}
				>
					Apply
				</button>
				<button
					type="button"
					class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
					onclick={clearAuditFilter}
				>
					Clear
				</button>
			</div>

			{#if auditError}
				<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-4">
					<p class="text-sm font-medium text-danger">Could not load audit logs</p>
					<p class="mt-1 text-sm text-neutral-700">{auditError}</p>
					<button
						type="button"
						class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
						onclick={() => loadAuditLogs(true)}
					>
						Retry
					</button>
				</div>
			{/if}

			{#if auditLoading && auditLogs.length === 0}
				<p class="text-sm text-neutral-500" role="status">Loading audit logs…</p>
			{:else if !auditError && auditLogs.length === 0}
				<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
					<p class="text-sm text-neutral-500">No audit log entries for this filter.</p>
				</div>
			{:else if auditLogs.length > 0}
				<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
					<table class="min-w-full divide-y divide-neutral-200 text-sm">
						<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
							<tr>
								<th class="px-4 py-2">Time</th>
								<th class="px-4 py-2">Company</th>
								<th class="px-4 py-2">User</th>
								<th class="px-4 py-2">Action</th>
								<th class="px-4 py-2">Entity</th>
								<th class="px-4 py-2" title="The row this action affected, when applicable.">Entity ID</th>
								<th class="px-4 py-2"></th>
							</tr>
						</thead>
						<tbody class="divide-y divide-neutral-100">
							{#each auditLogs as entry (entry.id)}
								<tr>
									<td class="px-4 py-2 text-neutral-600">{new Date(entry.created_at).toLocaleString()}</td>
									<td class="px-4 py-2 text-neutral-600">
										{#if entry.company_name}
											<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
												{entry.company_name}
											</span>
										{:else}
											<span class="text-xs text-neutral-400">Platform</span>
										{/if}
									</td>
									<td class="px-4 py-2 text-neutral-600">{entry.user_email ?? 'system'}</td>
									<td class="px-4 py-2 font-mono text-xs text-neutral-600">{entry.action}</td>
									<td class="px-4 py-2 text-neutral-600">{entry.entity}</td>
									<td class="px-4 py-2 font-mono text-xs text-neutral-500">{entry.entity_id ?? '—'}</td>
									<td class="px-4 py-2 text-right">
										{#if entry.diff}
											<button
												type="button"
												class="text-xs text-primary underline underline-offset-2"
												onclick={() => (auditExpandedId = auditExpandedId === entry.id ? null : entry.id)}
											>
												{auditExpandedId === entry.id ? 'Hide diff' : 'View diff'}
											</button>
										{/if}
									</td>
								</tr>
								{#if auditExpandedId === entry.id && entry.diff}
									<tr>
										<td colspan="7" class="bg-neutral-50 px-4 py-2">
											<pre class="overflow-x-auto text-xs text-neutral-700">{JSON.stringify(
													entry.diff,
													null,
													2
												)}</pre>
										</td>
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>

				{#if auditHasMore}
					<div class="mt-4 text-center">
						<button
							type="button"
							disabled={auditLoading}
							class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
							onclick={() => loadAuditLogs(false)}
						>
							{auditLoading ? 'Loading…' : 'Load more'}
						</button>
					</div>
				{/if}
			{/if}
		{/if}
	{/if}
</div>
