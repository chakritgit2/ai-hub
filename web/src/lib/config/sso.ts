// Dev SSO stand-in (PRD §7.1) until the real Phalcon SSO is reachable from here.
// Shared by the Sidebar's manual sign-in link and the 401 auto-redirect in api/client.ts.
export const ssoLoginUrl = import.meta.env.DEV
	? 'http://localhost:8999/'
	: 'https://aihub-fake-sso.advws.com/';
