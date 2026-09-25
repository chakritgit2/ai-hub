import { writable } from 'svelte/store';
import type { Snippet } from 'svelte';

export interface HeaderContent {
	/** Breadcrumb menu segment, e.g. "Agents" (rendered as "{company} / {menu}"). */
	menu: string;
	/** Large page title under the breadcrumb. */
	title: string;
	/** Optional right-aligned primary action button. */
	actions?: Snippet;
}

/**
 * Each +page.svelte sets this on init so the shared Header in +layout.svelte
 * can render the right breadcrumb/title/action without every route re-implementing
 * the header chrome itself.
 */
export const headerContent = writable<HeaderContent>({ menu: '', title: '' });
