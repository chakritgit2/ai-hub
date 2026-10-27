<script lang="ts">
	import { companyId, tenantId, sampleCompanies, type CompanyOption } from '$lib/stores/company';
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

	// Keeps the tenant select in sync with whichever company is actually active - covers
	// both the initial load and company_id being changed from outside this component
	// (e.g. restored from localStorage on a page that isn't this one). Written into the
	// shared tenantId store (not just local state) so other pages - so far just the
	// Agents list's tenant-pooled view - know which tenant is selected too.
	//
	// Gated on $currentUser being loaded: before /me resolves, `options` is the
	// placeholder `sampleCompanies` (no real tenant_id values at all), so `current` is
	// always undefined for a real persisted $companyId and this fell into the `else if`
	// branch, overwriting a perfectly valid persisted $tenantId with '' every hard
	// page load - the company_id/tenant_id race (confirmed live: a mutating request fired
	// in that window used the wrong id and 404'd). Once $currentUser is null there is no
	// real membership data to correct against yet, so there is nothing to do here.
	$effect(() => {
		if (!$currentUser) return;
		const current = options.find((c) => c.id === $companyId);
		if (current && current.tenant_id !== $tenantId) {
			tenantId.set(current.tenant_id ?? '');
		} else if (!current && tenantOptions.length > 0 && !tenantOptions.some((t) => t.id === $tenantId)) {
			tenantId.set(tenantOptions[0].id);
		}
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

	// $companyId can be left over from the pre-login sampleCompanies placeholder (or an old
	// company from a previous user) — neither is a real console.companies.id, so every
	// X-Company-Id-scoped request 404s until it's corrected. Keep it pointed at a company
	// the logged-in user actually belongs to as soon as we know what those are.
	//
	// Same $currentUser gate as the tenantId effect above, for the same reason: before
	// /me resolves, `options` is the sampleCompanies placeholder, so a real persisted
	// $companyId (e.g. restored from localStorage) never matches any of its ids and this
	// clobbered it with the placeholder's first id every hard page load - the brief
	// window any request fired in it would 404 with the wrong company. Nothing to correct
	// against until $currentUser actually loads.
	$effect(() => {
		if (!$currentUser) return;
		if (options.length > 0 && !options.some((c) => c.id === $companyId)) {
			companyId.set(options[0].id);
		}
	});
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
