import { writable } from 'svelte/store';
import type { Me } from '$lib/api/types';

// Static placeholder user until SSO (PRD §7.1) is wired up.
const placeholderUser: Me = {
	id: 'u_placeholder',
	email: 'developer@advws.com',
	is_platform_admin: false,
	companies: [
		{ company_id: 'c_advws', role: 'admin' },
		{ company_id: 'c_vending', role: 'developer' },
		{ company_id: 'c_retail', role: 'viewer' }
	]
};

export const currentUser = writable<Me>(placeholderUser);

export function roleForCompany(user: Me, companyId: string): string | undefined {
	return user.companies.find((c) => c.company_id === companyId)?.role;
}
