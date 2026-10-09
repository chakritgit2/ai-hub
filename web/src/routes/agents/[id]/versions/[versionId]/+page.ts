import type { PageLoad } from './$types';
import { getAgent, getAgentVersion, listAgentVersions } from '$lib/api/console';

export const load: PageLoad = async ({ params }) => {
	try {
		const [agent, version, versions] = await Promise.all([
			getAgent(params.id),
			getAgentVersion(params.id, params.versionId),
			listAgentVersions(params.id)
		]);
		return { agent, version, versions, loadError: null as string | null };
	} catch (e) {
		return {
			agent: null,
			version: null,
			versions: [],
			loadError: e instanceof Error ? e.message : 'Failed to load this version.'
		};
	}
};
