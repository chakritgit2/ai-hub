<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import type { Snippet } from 'svelte';
	import Sidebar from '$lib/components/layout/Sidebar.svelte';
	import Header from '$lib/components/layout/Header.svelte';
	import CompanySwitcher from '$lib/components/layout/CompanySwitcher.svelte';
	import { headerContent } from '$lib/stores/header';

	// Session restore on a hard reload now happens in +layout.ts's load() instead of here -
	// SvelteKit resolves that before this component (or any child route) ever mounts, which
	// is what closes the company_id race (see +layout.ts).
	let { children }: { children: Snippet } = $props();

	// This component only mounts once that load() resolves, so "we've mounted" is exactly
	// the signal the static #app-loading spinner in app.html is waiting for.
	onMount(() => {
		document.getElementById('app-loading')?.remove();
	});
</script>

<div class="flex h-screen overflow-hidden bg-bg">
	<Sidebar />
	<div class="flex flex-1 flex-col overflow-hidden">
		<div class="flex items-center justify-end gap-3 border-b border-neutral-200 bg-bg px-8 pt-4">
			<CompanySwitcher />
		</div>
		<Header menu={$headerContent.menu} title={$headerContent.title} actions={$headerContent.actions} />
		<main class="flex-1 overflow-y-auto px-8 py-6">
			{@render children()}
		</main>
	</div>
</div>
