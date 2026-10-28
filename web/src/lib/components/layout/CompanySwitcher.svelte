<script lang="ts">
	import {
		companyId,
		tenantId,
		sampleCompanies,
		syncCompanyFromUser,
		type CompanyOption
	} from '$lib/stores/company';
	import { currentUser } from '$lib/stores/auth';

	// Real memberships from /me once logged in (getMe() now resolves company/tenant
	// name+code, not just ids - see AuthService::syncUser()'s join against
	// console.tenants). Otherwise the pre-login placeholder list, so the switcher
	// renders something before a session exists - sampleCompanies has no tenant_id, so
	// it renders as a single flat list below (tenantOptions collapses to one group).
	let options = $derived<CompanyOption[]>(
		$currentUser
			? $currentUser.companies.map((c) => ({
					id: c.company_id,
					code: c.company_code,
					name: c.company_name,
					tenant_id: c.tenant_id,
					tenant_name: c.tenant_name
				}))
			: sampleCompanies
	);

	// Groups this user's own companies by tenant - this is pure client-side navigation
	// grouping, not a new access scope: the set of companies is still exactly
	// $currentUser.companies, same as before this switcher existed.
	let tenantOptions = $derived<{ id: string; name: string }[]>(
		Array.from(new Map(options.map((c) => [c.tenant_id ?? '', c.tenant_name ?? 'Companies'])).entries()).map(
			([id, name]) => ({ id, name })
		)
	);

	// Keeps companyId/tenantId pointed at a company the logged-in user actually belongs
	// to. The root +layout.ts load already runs this once (via refreshCurrentUser) before
	// any page's first fetch, which is what actually closes the company_id/tenant_id race
	// (a stale id 404s against console-api) - this effect just keeps it correct live, e.g.
	// if $currentUser changes without a full page reload. Gated on $currentUser being
	// loaded: before /me resolves, there's no real membership data to correct against yet.
	$effect(() => {
		if ($currentUser) syncCompanyFromUser($currentUser.companies);
	});

	// Deliberately no separate Company control - picking a tenant jumps straight to its
	// first company (by user request). A tenant with more than one company is only
	// reachable here via that first one; switching to a sibling company needs a
	// different entry point than this header switcher.
	function onTenantChange(event: Event) {
		const newTenantId = (event.currentTarget as HTMLSelectElement).value;
		tenantId.set(newTenantId);
		const first = options.find((c) => (c.tenant_id ?? '') === newTenantId);
		if (first) companyId.set(first.id);
	}
</script>

<label class="flex items-center gap-2 text-sm">
	<span class="sr-only">Tenant</span>
	<select
		class="rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm text-neutral-800
			shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
		value={$tenantId}
		onchange={onTenantChange}
	>
		{#each tenantOptions as tenant (tenant.id)}
			<option value={tenant.id}>{tenant.name}</option>
		{/each}
	</select>
</label>
