import { writable } from 'svelte/store';
import { browser } from '$app/environment';

const STORAGE_KEY = 'dynamiq-console.company-id';
const TENANT_STORAGE_KEY = 'dynamiq-console.tenant-id';

export interface CompanyOption {
	id: string;
	code: string;
	name: string;
	// Unset for the pre-login sampleCompanies placeholder below - real options (from
	// getMe()) always have these since console.companies.tenant_id is NOT NULL.
	tenant_id?: string;
	tenant_name?: string;
}

// Hardcoded sample companies for the skeleton; real data will come from getMe()/listCompanies().
export const sampleCompanies: CompanyOption[] = [
	{ id: 'c_advws', code: 'advws', name: 'ADVWS (internal)' },
	{ id: 'c_vending', code: 'vending', name: 'Vending Co., Ltd.' },
	{ id: 'c_retail', code: 'retail', name: 'Retail Partners Co.' }
];

function readInitial(key: string, fallback: string): string {
	if (!browser) return fallback;
	try {
		return localStorage.getItem(key) ?? fallback;
	} catch {
		// localStorage can throw (private browsing, disabled storage, etc.) —
		// this is only a per-viewer convenience, never load-bearing state.
		return fallback;
	}
}

function createPersistedStore(key: string, fallback: string) {
	const { subscribe, set, update } = writable<string>(readInitial(key, fallback));

	return {
		subscribe,
		set(value: string) {
			set(value);
			if (browser) {
				try {
					localStorage.setItem(key, value);
				} catch {
					// ignore — see note above
				}
			}
		},
		update
	};
}

/** Current company_id; every console-api request reads this to set X-Company-Id (PRD §7.2). */
export const companyId = createPersistedStore(STORAGE_KEY, sampleCompanies[0].id);

/**
 * Current tenant_id, set by CompanySwitcher. Only consumed so far by the Agents list
 * page's tenant-pooled view (a deliberately narrow pilot of "each menu shows only its own
 * tenant's data") - every other page still reads only `companyId` above, unaffected by
 * which tenant is selected here.
 */
export const tenantId = createPersistedStore(TENANT_STORAGE_KEY, '');

export function companyById(id: string): CompanyOption | undefined {
	return sampleCompanies.find((c) => c.id === id);
}
