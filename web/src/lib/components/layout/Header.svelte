<script lang="ts">
	import type { Snippet } from 'svelte';
	import { companyById, companyId } from '$lib/stores/company';
	import { currentUser } from '$lib/stores/auth';

	interface Props {
		/** Current menu name for the breadcrumb, e.g. "Agents". */
		menu: string;
		/** Page title, shown large under the breadcrumb. */
		title: string;
		/** Optional right-aligned primary action (a button, usually). */
		actions?: Snippet;
	}

	let { menu, title, actions }: Props = $props();

	// getMe() resolves a real company name/tenant per membership (AuthService::syncUser's
	// join against console.tenants) - prefer that for a logged-in session; companyById()
	// only ever finds the pre-login sampleCompanies placeholder, so it's just the fallback
	// before a session exists.
	let currentCompany = $derived($currentUser?.companies.find((c) => c.company_id === $companyId));
	let companyName = $derived(
		currentCompany
			? `${currentCompany.tenant_name} / ${currentCompany.company_name}`
			: (companyById($companyId)?.name ?? $companyId)
	);
</script>

<header class="flex items-start justify-between border-b border-neutral-200 bg-bg px-8 py-5">
	<div>
		<p class="text-xs text-neutral-500">
			<span>{companyName}</span>
			<span aria-hidden="true"> / </span>
			<span>{menu}</span>
		</p>
		<h1 class="mt-1 text-xl font-semibold text-neutral-900">{title}</h1>
	</div>
	{#if actions}
		<div class="shrink-0">
			{@render actions()}
		</div>
	{/if}
</header>
