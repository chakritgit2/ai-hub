import type { PageLoad } from './$types';
import { getWorkflow, getWorkflowVersion } from '$lib/api/console';

export const load: PageLoad = async ({ params }) => {
	try {
		const [workflow, version] = await Promise.all([
			getWorkflow(params.id),
			getWorkflowVersion(params.id, params.versionId)
		]);
		return { workflow, version, loadError: null as string | null };
	} catch (e) {
		return {
			workflow: null,
			version: null,
			loadError: e instanceof Error ? e.message : 'Failed to load this version.'
		};
	}
};
