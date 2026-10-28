import { get } from 'svelte/store';
import { authToken } from '$lib/stores/token';
import { refreshCurrentUser } from '$lib/stores/auth';

// Fully client-rendered SPA (adapter-static, fallback: index.html) — see svelte.config.js.
export const ssr = false;

// Resolves the logged-in user - and corrects companyId/tenantId to a real membership, see
// syncCompanyFromUser - before any child route's load()/onMount() fires. SvelteKit always
// resolves the root layout's load before mounting anything under it (even with ssr:false),
// so gating here closes the company_id race for every page at once: without it, a hard
// reload mounts e.g. agents/[id]'s +page.ts or datasets/[id]'s onMount using whatever
// companyId was last persisted (the pre-login placeholder, or nothing at all), which 404s
// against console-api until this resolves moments later - too late for pages with no
// retry effect of their own. Guarded on a token existing, same as the old +layout.svelte
// onMount this replaces: calling refreshCurrentUser with no token would 401 and bounce an
// unauthenticated visitor (e.g. mid dev-login, before it's parsed the token from the URL)
// straight to SSO before they ever get a chance to log in.
export async function load() {
	if (get(authToken)) {
		await refreshCurrentUser();
	}
}
