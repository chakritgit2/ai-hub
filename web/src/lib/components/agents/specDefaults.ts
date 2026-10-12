// Shared "blank"/"from a saved AgentVersion" constructors used by agents/new, agents/[id],
// and agents/[id]/versions/[versionId] - pulled out once a third call site made the
// duplication real (not speculative).
import type { AgentIdentity, AgentVersion, GuardrailsSpec, MemorySpec, ModelSpec } from '$lib/api/types';
import type { MemoryFormState, ModelFormState } from './AgentSpecTabs.svelte';

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
	return { connection_id: '', model: '', temperature: '', max_tokens: '' };
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

export function toModelFormState(m?: ModelSpec): ModelFormState {
	return {
		connection_id: m?.connection_id ?? '',
		model: m?.model ?? '',
		temperature: m?.temperature ?? '',
		max_tokens: m?.max_tokens ?? ''
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

export interface SpecFormState {
	identity: AgentIdentity;
	model: ModelFormState;
	selectedToolIds: string[];
	selectedSkillNames: string[];
	selectedKnowledgeBaseIds: string[];
	guardrails: GuardrailsSpec;
	memory: MemoryFormState;
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
		memory: toMemoryFormState(version?.spec.memory)
	};
}
