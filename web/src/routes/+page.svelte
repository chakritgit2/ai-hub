<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';

	onMount(() => {
		goto('/dashboard', { replaceState: true });
	});
</script>

<p class="text-sm text-neutral-500">Redirecting to the dashboard…</p>
