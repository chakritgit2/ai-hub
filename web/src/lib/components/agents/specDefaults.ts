// Shared "blank"/"from a saved AgentVersion" constructors used by agents/new, agents/[id],
// and agents/[id]/versions/[versionId] - pulled out once a third call site made the
// duplication real (not speculative).
import type { AgentIdentity, AgentVersion, GuardrailsSpec, MemorySpec, ModelSpec, PublishGateSpec } from '$lib/api/types';
import type { MemoryFormState, ModelFormState, PublishGateFormState } from './AgentSpecTabs.svelte';

export function blankIdentity(): AgentIdentity {
	return {
		display_name: '',
		name: '',
		owner: '',
		role: '',
		languages: ['th'],
		persona: '',
		tags: [],
		responsibilities: '',
		in_scope: '',
		out_of_scope: '',
		handoff: '',
		instructions: ''
	};
}

export function blankModelFormState(): ModelFormState {
	return {
		connection_id: '',
		model: '',
		temperature: '',
		max_tokens: '',
		fallback_connection_id: '',
		fallback_model: ''
	};
}

/** The raw (non-form) ModelSpec shape for a brand-new spec with nothing filled in yet -
 * distinct from blankModelFormState() since that's shaped for AgentSpecTabs's inputs
 * (string|number|null fields for the empty-vs-unset quirk), not for an API payload. */
export function blankModelSpec(): ModelSpec {
	return { connection_id: '', model: '' };
}

export function blankGuardrails(): GuardrailsSpec {
	return { input: [], output: [] };
}

/** Matches the backend's MemorySpec defaults (ai/app/services/agent_spec.py) - an agent
 * that never touches this tab behaves exactly as it does today (memory always on). */
export function blankMemorySpec(): MemorySpec {
	return { enabled: true, message_limit: null, save_mode: 'full' };
}

export function blankMemory(): MemoryFormState {
	return { enabled: true, message_limit: '', save_mode: 'full' };
}

/** Matches the backend's PublishGateSpec defaults (ai/app/services/agent_spec.py) -
 * opt-in, off by default, so an agent that never touches this tab publishes exactly
 * as it does today. */
export function blankPublishGateSpec(): PublishGateSpec {
	return { enabled: false, metric: null, threshold: null };
}

export function blankPublishGate(): PublishGateFormState {
	return { enabled: false, metric: '', threshold: '' };
}

export function toModelFormState(m?: ModelSpec): ModelFormState {
	return {
		connection_id: m?.connection_id ?? '',
		model: m?.model ?? '',
		temperature: m?.temperature ?? '',
		max_tokens: m?.max_tokens ?? '',
		fallback_connection_id: m?.fallback_connection_id ?? '',
		fallback_model: m?.fallback_model ?? ''
	};
}

/** Same string|number|null empty-vs-unset shape as toModelFormState - message_limit is an
 * optional positive int on the backend (MemorySpec), but an <input type="number"> needs to
 * round-trip through '' while the user is editing/clearing it. */
export function toMemoryFormState(m?: MemorySpec): MemoryFormState {
	return {
		enabled: m?.enabled ?? true,
		message_limit: m?.message_limit ?? '',
		save_mode: m?.save_mode ?? 'full'
	};
}

/** Same string|number|null empty-vs-unset shape as toMemoryFormState - threshold is an
 * optional float on the backend (PublishGateSpec) but an <input type="number"> needs to
 * round-trip through '' while the user is editing/clearing it. */
export function toPublishGateFormState(g?: PublishGateSpec): PublishGateFormState {
	return {
		enabled: g?.enabled ?? false,
		metric: g?.metric ?? '',
		threshold: g?.threshold ?? ''
	};
}

/** Turns a 400 publish_gate_failed body (AgentsController::checkPublishGate) into a
 * human message - shared by agents/new and agents/[id], the two pages that call
 * publishAgentVersion(). */
export function formatPublishGateError(body: {
	reason: string;
	metric?: string;
	threshold?: number;
	actual?: number;
}): string {
	if (body.reason === 'no_eval_run') {
		return 'No completed eval run exists yet for this agent version — run one (Evaluation) before publishing.';
	}
	if (body.reason === 'metric_not_scored') {
		return `The configured metric "${body.metric}" wasn't scored in the most recent eval run.`;
	}
	if (body.reason === 'below_threshold') {
		return `${body.metric} scored ${body.actual?.toFixed(3)}, below the required threshold of ${body.threshold}.`;
	}
	return 'Publishing was blocked by the publish gate.';
}

export interface SpecFormState {
	identity: AgentIdentity;
	model: ModelFormState;
	selectedToolIds: string[];
	selectedSkillNames: string[];
	selectedKnowledgeBaseIds: string[];
	guardrails: GuardrailsSpec;
	memory: MemoryFormState;
	publishGate: PublishGateFormState;
}

/** Builds editable form state from a saved version's spec, or blank defaults if there's none. */
export function formStateFromVersion(version: AgentVersion | null | undefined): SpecFormState {
	return {
		identity: version?.spec.identity ? { ...blankIdentity(), ...version.spec.identity } : blankIdentity(),
		model: toModelFormState(version?.spec.model),
		selectedToolIds: (version?.spec.tools ?? []).map((t) => t.tool_id),
		selectedSkillNames: version?.spec.skills ?? [],
		selectedKnowledgeBaseIds: (version?.spec.knowledge ?? []).map((k) => k.kb_id),
		guardrails: version?.spec.guardrails ?? blankGuardrails(),
		memory: toMemoryFormState(version?.spec.memory),
		publishGate: toPublishGateFormState(version?.spec.publish_gate)
	};
}
