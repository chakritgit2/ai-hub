<script lang="ts">
	import { companyId, sampleCompanies, type CompanyOption } from '$lib/stores/company';
	import { currentUser } from '$lib/stores/auth';

	function onChange(event: Event) {
		const target = event.currentTarget as HTMLSelectElement;
		companyId.set(target.value);
	}

	// Real memberships from /me once logged in (PRD §7.1) — /me has no company name yet
	// (just id + role), so this falls back to a short id until that's available.
	// Otherwise the pre-login placeholder list, so the switcher renders something before
	// a session exists.
	let options = $derived<CompanyOption[]>(
		$currentUser
			? $currentUser.companies.map((c) => ({
					id: c.company_id,
					code: c.company_id.slice(0, 8),
					name: `${c.company_id.slice(0, 8)}… (${c.role})`
				}))
			: sampleCompanies
	);
</script>

<label class="flex items-center gap-2 text-sm">
	<span class="sr-only">Company</span>
	<select
		class="rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm text-neutral-800
			shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
		value={$companyId}
		on:change={onChange}
	>
		{#each options as company (company.id)}
			<option value={company.id}>{company.name}</option>
		{/each}
	</select>
</label>
