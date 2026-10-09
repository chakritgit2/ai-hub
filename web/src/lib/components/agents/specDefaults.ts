// Shared "blank"/"from a saved AgentVersion" constructors used by agents/new, agents/[id],
// and agents/[id]/versions/[versionId] - pulled out once a third call site made the
// duplication real (not speculative).
import type { AgentIdentity, AgentVersion, GuardrailsSpec, ModelSpec } from '$lib/api/types';
import type { ModelFormState } from './AgentSpecTabs.svelte';

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

export function toModelFormState(m?: ModelSpec): ModelFormState {
	return {
		connection_id: m?.connection_id ?? '',
		model: m?.model ?? '',
		temperature: m?.temperature ?? '',
		max_tokens: m?.max_tokens ?? ''
	};
}

export interface SpecFormState {
	identity: AgentIdentity;
	model: ModelFormState;
	selectedToolIds: string[];
	selectedSkillNames: string[];
	guardrails: GuardrailsSpec;
}

/** Builds editable form state from a saved version's spec, or blank defaults if there's none. */
export function formStateFromVersion(version: AgentVersion | null | undefined): SpecFormState {
	return {
		identity: version?.spec.identity ? { ...blankIdentity(), ...version.spec.identity } : blankIdentity(),
		model: toModelFormState(version?.spec.model),
		selectedToolIds: (version?.spec.tools ?? []).map((t) => t.tool_id),
		selectedSkillNames: version?.spec.skills ?? [],
		guardrails: version?.spec.guardrails ?? blankGuardrails()
	};
}
