import { PUBLIC_CONSOLE_API_BASE_URL } from '$env/static/public';
import { get } from 'svelte/store';
import { companyId } from '$lib/stores/company';

export class ApiError extends Error {
	status: number;
	body: unknown;

	constructor(status: number, message: string, body?: unknown) {
		super(message);
		this.name = 'ApiError';
		this.status = status;
		this.body = body;
	}
}

export interface ApiFetchOptions extends Omit<RequestInit, 'body'> {
	/** JSON-serializable body. For non-JSON bodies, pass a RequestInit body directly via `rawBody`. */
	body?: unknown;
	rawBody?: BodyInit;
	/** Skip attaching X-Company-Id (e.g. for /me, /companies per PRD §9.1). */
	skipCompanyHeader?: boolean;
}

/**
 * Fetch wrapper for console-api (Phalcon), PRD §9.1.
 *
 * - Prefixes every call with PUBLIC_CONSOLE_API_BASE_URL.
 * - Injects X-Company-Id from the current company store on every request
 *   unless skipCompanyHeader is set (every /admin/v1/* route except /me and
 *   /companies requires it, checked against membership server-side).
 * - Throws ApiError on non-2xx responses so callers can branch on `.status`.
 */
export async function apiFetch<T = unknown>(path: string, opts: ApiFetchOptions = {}): Promise<T> {
	const { body, rawBody, skipCompanyHeader, headers, ...rest } = opts;

	const finalHeaders = new Headers(headers);
	if (!finalHeaders.has('Accept')) finalHeaders.set('Accept', 'application/json');

	if (!skipCompanyHeader) {
		const currentCompanyId = get(companyId);
		if (currentCompanyId) finalHeaders.set('X-Company-Id', currentCompanyId);
	}

	let requestBody: BodyInit | undefined = rawBody;
	if (body !== undefined) {
		finalHeaders.set('Content-Type', 'application/json');
		requestBody = JSON.stringify(body);
	}

	const base = PUBLIC_CONSOLE_API_BASE_URL ?? '';
	const url = `${base.replace(/\/$/, '')}${path.startsWith('/') ? path : `/${path}`}`;

	let response: Response;
	try {
		response = await fetch(url, {
			...rest,
			headers: finalHeaders,
			body: requestBody
		});
	} catch (err) {
		throw new ApiError(0, `Network error calling ${path}: ${(err as Error).message}`);
	}

	if (response.status === 204) {
		return undefined as T;
	}

	const contentType = response.headers.get('content-type') ?? '';
	const payload = contentType.includes('application/json')
		? await response.json().catch(() => undefined)
		: await response.text();

	if (!response.ok) {
		const message =
			(payload && typeof payload === 'object' && 'error' in payload && String(payload.error)) ||
			`Request to ${path} failed with ${response.status}`;
		throw new ApiError(response.status, message, payload);
	}

	return payload as T;
}
