import { writable } from 'svelte/store';
import { browser } from '$app/environment';

const STORAGE_KEY = 'dynamiq-console.company-id';

export interface CompanyOption {
	id: string;
	code: string;
	name: string;
}

// Hardcoded sample companies for the skeleton; real data will come from getMe()/listCompanies().
export const sampleCompanies: CompanyOption[] = [
	{ id: 'c_advws', code: 'advws', name: 'ADVWS (internal)' },
	{ id: 'c_vending', code: 'vending', name: 'Vending Co., Ltd.' },
	{ id: 'c_retail', code: 'retail', name: 'Retail Partners Co.' }
];

function readInitialCompanyId(): string {
	if (!browser) return sampleCompanies[0].id;
	try {
		return localStorage.getItem(STORAGE_KEY) ?? sampleCompanies[0].id;
	} catch {
		// localStorage can throw (private browsing, disabled storage, etc.) —
		// this is only a per-viewer convenience, never load-bearing state.
		return sampleCompanies[0].id;
	}
}

function createCompanyStore() {
	const { subscribe, set, update } = writable<string>(readInitialCompanyId());

	return {
		subscribe,
		set(value: string) {
			set(value);
			if (browser) {
				try {
					localStorage.setItem(STORAGE_KEY, value);
				} catch {
					// ignore — see note above
				}
			}
		},
		update
	};
}

/** Current company_id; every console-api request reads this to set X-Company-Id (PRD §7.2). */
export const companyId = createCompanyStore();

export function companyById(id: string): CompanyOption | undefined {
	return sampleCompanies.find((c) => c.id === id);
}
