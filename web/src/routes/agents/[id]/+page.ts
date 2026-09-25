import type { PageLoad } from './$types';
import { getAgent, listAgentVersions } from '$lib/api/console';

export const load: PageLoad = async ({ params }) => {
	try {
		const [agent, versions] = await Promise.all([
			getAgent(params.id),
			listAgentVersions(params.id)
		]);
		return { agent, versions, loadError: null as string | null };
	} catch (e) {
		return {
			agent: null,
			versions: [],
			loadError: e instanceof Error ? e.message : 'Failed to load this agent.'
		};
	}
};
