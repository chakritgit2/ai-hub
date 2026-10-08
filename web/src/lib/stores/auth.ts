import { writable } from "svelte/store";
import { browser } from "$app/environment";
import type { Me } from "$lib/api/types";
import { getMe } from "$lib/api/console";
import { ApiError } from "$lib/api/client";
import { ssoLoginUrl } from "$lib/config/sso";
import { authToken } from "$lib/stores/token";

export { authToken };

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
  } catch (err) {
    // Only a real 401 means the session is gone — a network blip or 5xx
    // shouldn't wipe out a user we know is still logged in.
    if (err instanceof ApiError && err.status === 401) {
      currentUser.set(null);
    }
  }
}

export function roleForCompany(
  user: Me | null,
  companyId: string,
): string | undefined {
  return user?.companies.find((c) => c.company_id === companyId)?.role;
}

/** Clears the session and sends the browser back to SSO (same destination as the 401 auto-redirect in api/client.ts). */
export function logout(): void {
  authToken.set(null);
  currentUser.set(null);
  if (browser) window.location.href = ssoLoginUrl;
}
