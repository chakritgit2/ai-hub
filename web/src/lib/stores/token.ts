import { writable } from 'svelte/store';
import { browser } from '$app/environment';

const STORAGE_KEY = 'dynamiq-console.auth-token';

// Split out from stores/auth.ts so client.ts (which needs the token on every request)
// doesn't have to import auth.ts, which itself imports the api client — that cycle
// would leave one side of the import with an undefined binding at module-init time.

function readInitialToken(): string | null {
	if (!browser) return null;
	try {
		return localStorage.getItem(STORAGE_KEY);
	} catch {
		return null;
	}
}

function createAuthTokenStore() {
	const { subscribe, set } = writable<string | null>(readInitialToken());

	return {
		subscribe,
		set(value: string | null) {
			set(value);
			if (!browser) return;
			try {
				if (value) localStorage.setItem(STORAGE_KEY, value);
				else localStorage.removeItem(STORAGE_KEY);
			} catch {
				// per-viewer convenience only — see company store for the same pattern
			}
		}
	};
}

/** The SSO JWT (PRD §7.1), sent as `Authorization: Bearer` on every console-api request. */
export const authToken = createAuthTokenStore();
