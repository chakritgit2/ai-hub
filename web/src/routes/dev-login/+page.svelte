<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { authToken } from '$lib/stores/token';
	import { refreshCurrentUser } from '$lib/stores/auth';
	import { ssoLoginUrl } from '$lib/config/sso';

	// Landing point for console-api/dev/fake-sso.php's redirect — a stand-in for the
	// existing Phalcon system's SSO (PRD §7.1) until that's actually reachable. Not a
	// route a real login flow would use; only fake-sso.php links here.
	let status = $state('Signing in…');

	onMount(async () => {
		const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''));
		const token = hash.get('token');
		if (!token) {
			status = `No token in the URL — start from the SSO login page (${ssoLoginUrl}).`;
			return;
		}

		authToken.set(token);
		await refreshCurrentUser();
		await goto('/dashboard', { replaceState: true });
	});
</script>

<p class="text-sm text-neutral-500">{status}</p>
