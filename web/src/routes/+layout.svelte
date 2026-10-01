<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import type { Snippet } from 'svelte';
	import Sidebar from '$lib/components/layout/Sidebar.svelte';
	import Header from '$lib/components/layout/Header.svelte';
	import CompanySwitcher from '$lib/components/layout/CompanySwitcher.svelte';
	import { headerContent } from '$lib/stores/header';
	import { authToken } from '$lib/stores/token';
	import { refreshCurrentUser } from '$lib/stores/auth';

	let { children }: { children: Snippet } = $props();

	// Restores the session on a hard reload — /dev-login is the only place that sets
	// authToken directly, this just re-fetches /me for whatever it already has stored.
	onMount(() => {
		if ($authToken) refreshCurrentUser();
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
