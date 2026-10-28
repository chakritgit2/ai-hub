import { get, writable } from 'svelte/store';
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
 * Current tenant_id, set by CompanySwitcher. Read by every tenant-pooled list page
 * (option A pilot - Agents, Connections, Deployments, Workflows, Tools, Skills, Knowledge
 * Bases, Datasets, Runs, API Keys, Approvals, Evaluation) to fetch across every company
 * under it; every other page (detail/edit/create) still reads only `companyId` above.
 */
export const tenantId = createPersistedStore(TENANT_STORAGE_KEY, '');

export function companyById(id: string): CompanyOption | undefined {
	return sampleCompanies.find((c) => c.id === id);
}

/**
 * Points `companyId` at a specific company and runs `fn()` - every X-Company-Id-scoped
 * mutator (api/client.ts) reads `companyId` at call time, but a row on a tenant-pooled
 * list page can belong to any company under the tenant, not just whichever one is
 * currently selected. Used by every tenant-pooled page with an inline per-row mutator
 * (Connections, Tools, Skills, API Keys, Approvals, Evaluation's compare); pages that
 * instead navigate to a single-company-scoped detail route call `companyId.set(...)`
 * directly before navigating (see agents/+page.svelte's openAgent()), since there's no
 * `fn()` to wrap there.
 */
export function withCompany<T>(id: string, fn: () => Promise<T>): Promise<T> {
	companyId.set(id);
	return fn();
}

/**
 * Points `companyId`/`tenantId` at a company the user actually belongs to, called right
 * after `getMe()` resolves (see stores/auth.ts's refreshCurrentUser and
 * CompanySwitcher.svelte). `companyId` can be left over from the pre-login
 * `sampleCompanies` placeholder or a previous user's company - neither is a real
 * console.companies.id, so every X-Company-Id-scoped request 404s until this runs. Called
 * from the root `+layout.ts` load (which SvelteKit always resolves before any child
 * page's load()/onMount() fires) so every page sees a corrected id from its very first
 * fetch, instead of each page racing it independently.
 */
export function syncCompanyFromUser(companies: Array<{ company_id: string; tenant_id: string }>): void {
	if (companies.length === 0) return;
	const current = companies.find((c) => c.company_id === get(companyId));
	if (current) {
		if (current.tenant_id !== get(tenantId)) tenantId.set(current.tenant_id);
		return;
	}
	companyId.set(companies[0].company_id);
	tenantId.set(companies[0].tenant_id);
}
