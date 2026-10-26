<script lang="ts">
	import { companyId, sampleCompanies, type CompanyOption } from '$lib/stores/company';
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

	// Groups this user's own companies by tenant, so switching tenant narrows the second
	// dropdown instead of scrolling one long flat list - this is pure client-side
	// navigation grouping, not a new access scope: the set of companies shown is still
	// exactly $currentUser.companies, the same set the plain list showed before.
	let tenantOptions = $derived<{ id: string; name: string }[]>(
		Array.from(new Map(options.map((c) => [c.tenant_id ?? '', c.tenant_name ?? 'Companies'])).entries()).map(
			([id, name]) => ({ id, name })
		)
	);

	let selectedTenantId = $state<string>('');

	// Keeps the tenant select in sync with whichever company is actually active - covers
	// both the initial load and company_id being changed from outside this component
	// (e.g. restored from localStorage on a page that isn't this one).
	$effect(() => {
		const current = options.find((c) => c.id === $companyId);
		if (current && current.tenant_id !== selectedTenantId) {
			selectedTenantId = current.tenant_id ?? '';
		} else if (!current && tenantOptions.length > 0 && !tenantOptions.some((t) => t.id === selectedTenantId)) {
			selectedTenantId = tenantOptions[0].id;
		}
	});

	let companiesInTenant = $derived(options.filter((c) => (c.tenant_id ?? '') === selectedTenantId));

	function onTenantChange(event: Event) {
		selectedTenantId = (event.currentTarget as HTMLSelectElement).value;
		const first = options.find((c) => (c.tenant_id ?? '') === selectedTenantId);
		if (first) companyId.set(first.id);
	}

	function onCompanyChange(event: Event) {
		companyId.set((event.currentTarget as HTMLSelectElement).value);
	}

	// $companyId can be left over from the pre-login sampleCompanies placeholder (or an old
	// company from a previous user) — neither is a real console.companies.id, so every
	// X-Company-Id-scoped request 404s until it's corrected. Keep it pointed at a company
	// the logged-in user actually belongs to as soon as we know what those are.
	$effect(() => {
		if (options.length > 0 && !options.some((c) => c.id === $companyId)) {
			companyId.set(options[0].id);
		}
	});
</script>

<div class="flex items-center gap-2">
	{#if tenantOptions.length > 1}
		<label class="flex items-center gap-2 text-sm">
			<span class="sr-only">Tenant</span>
			<select
				class="rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm text-neutral-800
					shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				value={selectedTenantId}
				onchange={onTenantChange}
			>
				{#each tenantOptions as tenant (tenant.id)}
					<option value={tenant.id}>{tenant.name}</option>
				{/each}
			</select>
		</label>
	{/if}
	<label class="flex items-center gap-2 text-sm">
		<span class="sr-only">Company</span>
		<select
			class="rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm text-neutral-800
				shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			value={$companyId}
			onchange={onCompanyChange}
		>
			{#each companiesInTenant as company (company.id)}
				<option value={company.id}>{company.name}</option>
			{/each}
		</select>
	</label>
</div>
