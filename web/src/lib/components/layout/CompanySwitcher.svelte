<script lang="ts">
	import { companyId, sampleCompanies } from '$lib/stores/company';

	function onChange(event: Event) {
		const target = event.currentTarget as HTMLSelectElement;
		companyId.set(target.value);
	}
</script>

<label class="flex items-center gap-2 text-sm">
	<span class="sr-only">Company</span>
	<select
		class="rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm text-neutral-800
			shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
		value={$companyId}
		on:change={onChange}
	>
		{#each sampleCompanies as company (company.id)}
			<option value={company.id}>{company.name}</option>
		{/each}
	</select>
</label>
