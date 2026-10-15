import type { PageLoad } from './$types';
import { getWorkflow, listWorkflowVersions } from '$lib/api/console';

export const load: PageLoad = async ({ params }) => {
	try {
		const [workflow, versions] = await Promise.all([
			getWorkflow(params.id),
			listWorkflowVersions(params.id)
		]);
		return { workflow, versions, loadError: null as string | null };
	} catch (e) {
		return {
			workflow: null,
			versions: [],
			loadError: e instanceof Error ? e.message : 'Failed to load this workflow.'
		};
	}
};
