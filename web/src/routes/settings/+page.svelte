<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listCompanyUsers,
		listEgressAllowlist,
		listCompanies
	} from '$lib/api/console';
	import type { Placeholder, Company } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { currentUser } from '$lib/stores/auth';

	headerContent.set({ menu: 'Settings', title: 'Settings' });

	type TabId = 'users' | 'egress' | 'companies' | 'pricing';

	let activeTab = $state<TabId>('users');

	let users = $state<Placeholder[]>([]);
	let usersLoading = $state(true);
	let usersError = $state<string | null>(null);

	let egress = $state<Placeholder[]>([]);
	let egressLoading = $state(true);
	let egressError = $state<string | null>(null);

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
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{usersError}</div>
		{:else if users.length === 0}
			<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
				<p class="text-sm text-neutral-500">No users synced from SSO yet.</p>
			</div>
		{:else}
			<pre class="rounded-lg border border-neutral-200 bg-white p-4 text-xs">{JSON.stringify(users, null, 2)}</pre>
		{/if}
	{:else if activeTab === 'egress'}
		{#if egressLoading}
			<p class="text-sm text-neutral-500" role="status">Loading egress allowlist…</p>
		{:else if egressError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{egressError}</div>
		{:else if egress.length === 0}
			<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
				<p class="text-sm text-neutral-500">
					No egress allowlist entries yet — connections cannot reach any host until one is added
					(PRD §7.4).
				</p>
			</div>
		{:else}
			<pre class="rounded-lg border border-neutral-200 bg-white p-4 text-xs">{JSON.stringify(egress, null, 2)}</pre>
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
