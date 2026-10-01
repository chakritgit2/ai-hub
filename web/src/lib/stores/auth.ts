import { writable } from 'svelte/store';
import { browser } from '$app/environment';
import type { Me } from '$lib/api/types';
import { getMe } from '$lib/api/console';

export { authToken } from '$lib/stores/token';

/** The logged-in user, fetched from GET /me (PRD §9.1). Null until refreshCurrentUser() succeeds. */
export const currentUser = writable<Me | null>(null);

/**
 * Fetches /me with the current auth token and updates currentUser — call after
 * authToken.set() (see routes/dev-login) and once on app load if a token already exists
 * (see +layout.svelte).
 */
export async function refreshCurrentUser(): Promise<void> {
	if (!browser) return;
	try {
		currentUser.set(await getMe());
	} catch {
		currentUser.set(null);
	}
}

export function roleForCompany(user: Me | null, companyId: string): string | undefined {
	return user?.companies.find((c) => c.company_id === companyId)?.role;
}
