<script lang="ts">
	import type { Snippet } from 'svelte';
	import { companyById, companyId } from '$lib/stores/company';

	interface Props {
		/** Current menu name for the breadcrumb, e.g. "Agents". */
		menu: string;
		/** Page title, shown large under the breadcrumb. */
		title: string;
		/** Optional right-aligned primary action (a button, usually). */
		actions?: Snippet;
	}

	let { menu, title, actions }: Props = $props();

	let companyName = $derived(companyById($companyId)?.name ?? $companyId);
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
