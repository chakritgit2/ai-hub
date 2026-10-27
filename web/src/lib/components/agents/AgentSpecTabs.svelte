<script lang="ts">
	import { onMount } from 'svelte';
	import {
		listConnections,
		listTools,
		listSkills,
		listKnowledgeBases,
		listConnectionModels,
		testConnectionModel
	} from '$lib/api/console';
	import type {
		AgentIdentity,
		Connection,
		GuardrailCheckSpec,
		GuardrailCheckType,
		GuardrailsSpec,
		KnowledgeBase,
		MemorySaveMode,
		MemorySpec,
		ModelSpec,
		PublishGateSpec,
		Skill,
		Tool
	} from '$lib/api/types';
	import { EVAL_METRICS } from '$lib/api/types';

	// Only connection types the compiler can actually build an LLM for today
	// (ai/app/integrations/dynamiq_adapter.py's _CONNECTION_BUILDERS/_LLM_TYPES) - anything
	// else fails at publish time with "Unsupported connection type", so there's no point
	// offering it here. Update this allowlist if the compiler gains another provider.
	const USABLE_CONNECTION_TYPES = new Set(['dynamiq.connections.OpenAI', 'openai']);

	// kind: "http" and kind: "builtin" (Tavily/Exa web search) tools can actually be built
	// into a real node today (ai/app/services/compiler.py's _resolve_tools) - kind:
	// "python" references fail compilation outright for every role, so there's no point
	// offering it here. _resolve_tools also rejects an http tool with no config.url and a
	// builtin tool with no config.provider, so those are checked too (isToolUsable below),
	// not just the kind.
	const USABLE_TOOL_KINDS = new Set(['http', 'builtin']);

	function isToolUsable(tool: Tool): boolean {
		if (!USABLE_TOOL_KINDS.has(tool.kind) || !tool.enabled) {
			return false;
		}
		if (tool.kind === 'http') {
			return !!tool.config?.url;
		}
		return !!tool.config?.provider;
	}

	// A skill is attachable iff it has a published version (ai/app/services/compiler.py's
	// _validate_skills checks doc.skills names against list_published_skills(company_id),
	// which is gated on skill_versions.is_published, not skills.status - that column is
	// vestigial and never read by the compiler). Gating on latest_version.is_published is
	// sufficient but not strictly complete: a skill whose newest draft sits on top of an
	// older published version would still compile fine server-side via that older version,
	// but the Skill type here only exposes latest_version, so the UI can't tell that case
	// apart from "never published" without an extra per-skill fetch. Treating both as
	// "not usable" is the safe default - it may hide a name that would actually compile,
	// but never offers one that won't.
	function isSkillUsable(skill: Skill): boolean {
		return skill.latest_version.is_published;
	}

	// Only retrieval_mode: "vector" knowledge bases can be attached to an agent today
	// (ai/app/services/compiler.py's _resolve_knowledge) - Dynamiq's native
	// VectorStoreRetriever node (the only agent-attachable retrieval node this project has)
	// is vector-only; "hybrid" KBs (app/services/kb_hybrid.py's hand-rolled RRF fusion) have
	// no equivalent node, so they fail compilation outright rather than silently behaving
	// like a vector-only KB at run time.
	function isKnowledgeBaseUsable(kb: KnowledgeBase): boolean {
		return kb.retrieval_mode === 'vector';
	}

	// Mirrors ai/app/services/agent_spec.py's own constants exactly (_OUTPUT_ONLY_CHECK_TYPES,
	// _NO_MASK_CHECK_TYPES) - the check-type dropdowns below only offer what the compiler will
	// actually accept, same reasoning as isToolUsable/isSkillUsable above: don't let the user
	// pick a combination that's only going to fail at publish time.
	const ALL_CHECK_TYPES: GuardrailCheckType[] = ['max_length', 'regex_blocklist', 'pii', 'valid_json', 'valid_choices'];
	const OUTPUT_ONLY_CHECK_TYPES = new Set<GuardrailCheckType>(['valid_json', 'valid_choices']);
	const NO_MASK_CHECK_TYPES = new Set<GuardrailCheckType>(['max_length', 'valid_json', 'valid_choices']);
	const INPUT_CHECK_TYPES = ALL_CHECK_TYPES.filter((t) => !OUTPUT_ONLY_CHECK_TYPES.has(t));

	// `choicesText` is a UI-only raw text buffer for the comma-separated Choices input -
	// same reason identity.languages/tags get a separate languagesInput/tagsInput buffer
	// instead of binding the input directly to a derived `.join(', ')` of the array: a
	// one-way `value={array.join(', ')}` immediately collapses a just-typed trailing comma
	// (the array re-derives without the empty trailing token), eating the delimiter the
	// user needs to start a second item. Stripped out again by buildGuardrailsSpec() before
	// the spec is sent - the backend's GuardrailCheckSpec ignores unknown fields anyway
	// (model_config = ConfigDict(extra="ignore")), but there's no reason to send it.
	type GuardrailCheckRow = GuardrailCheckSpec & { choicesText?: string };

	function newGuardrailCheck(allowedTypes: GuardrailCheckType[]): GuardrailCheckRow {
		return { check_type: allowedTypes[0], action: 'flag', on_error: 'flag' };
	}

	/** Clears whichever type-specific fields don't apply to `newType`, so switching a check's
	 * type away and back doesn't leave stale data from the previous type riding along. */
	function resetTypeSpecificFields(check: GuardrailCheckRow, newType: GuardrailCheckType): void {
		if (newType !== 'max_length') check.max_length = null;
		if (newType !== 'regex_blocklist') check.pattern = null;
		if (newType !== 'valid_choices') {
			check.choices = null;
			check.choicesText = undefined;
		}
		if (check.action === 'mask' && NO_MASK_CHECK_TYPES.has(newType)) check.action = 'flag';
	}

	const isUnsetValue = (value: string | number | null | undefined): boolean =>
		value === '' || value === null || value === undefined;

	/** Flags the regex constructs `new RegExp()` happily compiles but Python's `re` module -
	 * what the backend actually compiles against (ai/app/services/agent_spec.py:98) -
	 * rejects or disagrees with, so the mismatch surfaces here instead of as a generic
	 * compile error at publish time. Not exhaustive (full JS/Python regex-dialect parity
	 * isn't practical client-side), just the two mismatches that are both common and
	 * reliably detectable without writing a real parser. */
	function findPythonRegexIncompatibility(pattern: string): string | null {
		// `\p{...}`/`\P{...}` Unicode property escapes: `new RegExp` compiles these even
		// without the `u` flag (silently falling back to a plain "p"/"P" match), but
		// Python's `re` has no such escape at all and always raises "bad escape \p".
		if (/\\[pP]\{[^}]*\}/.test(pattern)) {
			return "pattern uses \\p{...}/\\P{...} (Unicode property escapes), which Python's regex engine doesn't support.";
		}

		// Variable-width look-behind, e.g. (?<=\d+) or (?<!a{2,4}): JS look-behind has no
		// width restriction, but Python's `re` requires every look-behind to be fixed-width
		// and raises "look-behind requires fixed-width pattern" otherwise.
		const lookbehindVariableWidth = "pattern has a variable-length look-behind (?<=...)/(?<!...) - Python requires look-behind to be fixed-width.";
		for (const match of pattern.matchAll(/\(\?<[=!]((?:\\.|[^()\\]|\([^)]*\))*)\)/g)) {
			const body = match[1];
			if (/[*+]/.test(body)) {
				return lookbehindVariableWidth;
			}
			for (const q of body.matchAll(/\{(\d+)(,)(\d*)\}/g)) {
				const [, min, , max] = q;
				if (max === '' || max !== min) {
					return lookbehindVariableWidth;
				}
			}
		}

		return null;
	}

	/** Validates + cleans every check (mirrors buildModelSpec's "return the error message
	 * naming what failed, or the built value" shape) - matches exactly what
	 * ai/app/services/agent_spec.py's GuardrailCheckSpec._validate_type_specific_params
	 * requires per check_type, so a spec this returns never fails compilation for a
	 * guardrails-shaped reason. */
	function cleanGuardrailCheck(check: GuardrailCheckRow, sectionLabel: string, index: number): GuardrailCheckSpec | string {
		const where = `Guardrails (${sectionLabel} check #${index + 1})`;
		if (check.action === 'mask' && NO_MASK_CHECK_TYPES.has(check.check_type)) {
			return `${where}: action 'mask' isn't supported for a ${check.check_type} check.`;
		}
		if (check.check_type === 'max_length') {
			if (isUnsetValue(check.max_length) || !Number.isInteger(Number(check.max_length)) || Number(check.max_length) <= 0) {
				return `${where}: max length must be a whole number greater than 0.`;
			}
		}
		if (check.check_type === 'regex_blocklist') {
			if (!check.pattern?.trim()) {
				return `${where}: pattern is required.`;
			}
			try {
				new RegExp(check.pattern);
			} catch {
				return `${where}: pattern is not a valid regular expression.`;
			}
			const incompatibility = findPythonRegexIncompatibility(check.pattern);
			if (incompatibility) {
				return `${where}: ${incompatibility}`;
			}
		}
		let choices: string[] | null = null;
		if (check.check_type === 'valid_choices') {
			choices = (check.choicesText ?? (check.choices ?? []).join(', '))
				.split(',')
				.map((s) => s.trim())
				.filter(Boolean);
			if (choices.length === 0) {
				return `${where}: at least one choice is required.`;
			}
		}
		const { choicesText: _choicesText, ...rest } = check;
		return { ...rest, choices };
	}

	/** Returns the built, backend-valid spec, or an error message naming which check failed. */
	export function buildGuardrailsSpec(): GuardrailsSpec | string {
		const cleaned: Record<'input' | 'output', GuardrailCheckSpec[]> = { input: [], output: [] };
		for (const section of ['input', 'output'] as const) {
			for (let i = 0; i < guardrails[section].length; i++) {
				const result = cleanGuardrailCheck(guardrails[section][i] as GuardrailCheckRow, section, i);
				if (typeof result === 'string') return result;
				cleaned[section].push(result);
			}
		}
		return cleaned;
	}

	export type ModelFormState = {
		connection_id: string;
		model: string;
		temperature: string | number | null;
		max_tokens: string | number | null;
		fallback_connection_id: string;
		fallback_model: string;
	};

	type TabId =
		| 'identity'
		| 'model'
		| 'tools'
		| 'knowledge'
		| 'skills'
		| 'memory'
		| 'guardrails'
		| 'publish_gate'
		| 'advanced';

	// Only the metrics an eval run can actually score (PRD §6.7 / ai/app/services/
	// eval_runner.py's UNSUPPORTED_EXECUTION_METRICS) - configuring a gate against
	// llm_evaluator/python_evaluator would mean it can never be satisfied.
	const GATE_METRICS = EVAL_METRICS.filter((m) => m.executable);

	interface Tab {
		id: TabId;
		label: string;
		/** Only tabs with a real form here are enabled; the rest note their PRD phase/section. */
		enabled: boolean;
		note?: string;
	}

	// Order per PRD §6.1: Identity, Model, Tools, Knowledge, Skills, Memory, Guardrails, Advanced.
	const tabs: Tab[] = [
		{ id: 'identity', label: 'Identity', enabled: true },
		{ id: 'model', label: 'Model', enabled: true },
		{ id: 'tools', label: 'Tools', enabled: true },
		{ id: 'knowledge', label: 'Knowledge', enabled: true },
		{ id: 'skills', label: 'Skills', enabled: true },
		{ id: 'memory', label: 'Memory', enabled: true },
		{ id: 'guardrails', label: 'Guardrails', enabled: true },
		{ id: 'publish_gate', label: 'Publish Gate', enabled: true },
		{ id: 'advanced', label: 'Advanced', enabled: true }
	];

	let activeTab = $state<TabId>('identity');

	let {
		identity = $bindable(),
		model = $bindable(),
		selectedToolIds = $bindable(),
		selectedSkillNames = $bindable(),
		selectedKnowledgeBaseIds = $bindable(),
		guardrails = $bindable(),
		memory = $bindable(),
		publishGate = $bindable(),
		readonly = false,
		compiledDefinition = null,
		isAdmin = false
	}: {
		identity: AgentIdentity;
		model: ModelFormState;
		selectedToolIds: string[];
		selectedSkillNames: string[];
		selectedKnowledgeBaseIds: string[];
		guardrails: GuardrailsSpec;
		memory: MemoryFormState;
		publishGate: PublishGateFormState;
		readonly?: boolean;
		compiledDefinition?: Record<string, unknown> | null;
		isAdmin?: boolean;
	} = $props();

	// Presentation-only comma-separated buffers for the two array fields on `identity`.
	// formStateFromVersion() (agents/[id] and agents/new) always builds a brand-new
	// `identity` object when the parent loads a different version, while typing into this
	// component only mutates the existing object's fields - so re-deriving these buffers
	// whenever the `identity` object reference changes (and only then) keeps them in sync
	// with a newly-loaded version without clobbering what the user is actively typing.
	let languagesInput = $state(identity.languages.join(', '));
	let tagsInput = $state((identity.tags ?? []).join(', '));
	let syncedIdentity = identity;
	$effect(() => {
		if (identity !== syncedIdentity) {
			syncedIdentity = identity;
			languagesInput = identity.languages.join(', ');
			tagsInput = (identity.tags ?? []).join(', ');
		}
	});

	export function syncIdentityArrays(): void {
		identity.languages = languagesInput
			.split(',')
			.map((l) => l.trim())
			.filter(Boolean);
		identity.tags = tagsInput
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean);
	}

	const isUnset = (value: string | number | null): boolean => value === '' || value === null;

	/** Returns the built spec, or an error message naming which check actually failed. */
	export function buildModelSpec(): ModelSpec | string {
		if (!model.connection_id || !model.model.trim()) {
			return 'Model: please select a connection and enter a model name.';
		}
		if (!isUnset(model.max_tokens) && (!Number.isInteger(Number(model.max_tokens)) || Number(model.max_tokens) <= 0)) {
			return 'Model: max tokens must be a whole number greater than 0.';
		}
		const hasFallbackConnection = model.fallback_connection_id.trim() !== '';
		const hasFallbackModel = model.fallback_model.trim() !== '';
		if (hasFallbackConnection !== hasFallbackModel) {
			return 'Model: fallback needs both a connection and a model name.';
		}
		return {
			connection_id: model.connection_id,
			model: model.model.trim(),
			...(!isUnset(model.temperature) ? { temperature: Number(model.temperature) } : {}),
			...(!isUnset(model.max_tokens) ? { max_tokens: Number(model.max_tokens) } : {}),
			...(hasFallbackConnection
				? { fallback_connection_id: model.fallback_connection_id, fallback_model: model.fallback_model.trim() }
				: {})
		};
	}

	export type MemoryFormState = {
		enabled: boolean;
		message_limit: string | number | null;
		save_mode: MemorySaveMode;
	};

	/** Mirrors ai/app/services/agent_spec.py's MemorySpec validation (message_limit must be a
	 * positive int) - same "return the error message, or the built value" shape as
	 * buildModelSpec/buildGuardrailsSpec. Validation is skipped while memory is disabled, since
	 * the message_limit input is only rendered (and so only fixable) while `memory.enabled` is
	 * true - a stale invalid value left over from before the user unchecked it must not block
	 * saving a spec that has memory off. That's validation only, not a reset: the value itself
	 * is still carried through unchanged (same as save_mode below) so re-enabling memory later
	 * doesn't silently lose a previously-configured limit. */
	export function buildMemorySpec(): MemorySpec | string {
		if (
			memory.enabled &&
			!isUnset(memory.message_limit) &&
			(!Number.isInteger(Number(memory.message_limit)) || Number(memory.message_limit) <= 0)
		) {
			return 'Memory: message limit must be a whole number greater than 0.';
		}
		return {
			enabled: memory.enabled,
			message_limit: isUnset(memory.message_limit) ? null : Number(memory.message_limit),
			save_mode: memory.save_mode
		};
	}

	export type PublishGateFormState = {
		enabled: boolean;
		metric: string;
		threshold: string | number;
	};

	/** Mirrors ai/app/services/agent_spec.py's PublishGateSpec validation (metric/
	 * threshold required while enabled) - same "return the error message, or the built
	 * value" shape as buildModelSpec/buildMemorySpec. Unlike buildMemorySpec (which
	 * preserves message_limit through a disable/re-enable cycle), a disabled gate's
	 * metric/threshold are cleared unconditionally - a gate that's off is never checked
	 * either way, so there's no behavior to preserve by keeping stale values around. */
	export function buildPublishGateSpec(): PublishGateSpec | string {
		if (!publishGate.enabled) {
			return { enabled: false, metric: null, threshold: null };
		}
		if (!publishGate.metric) {
			return 'Publish gate: please select a metric.';
		}
		if (isUnset(publishGate.threshold) || Number.isNaN(Number(publishGate.threshold))) {
			return 'Publish gate: please enter a numeric threshold.';
		}
		return { enabled: true, metric: publishGate.metric, threshold: Number(publishGate.threshold) };
	}

	let connections = $state<Connection[]>([]);
	let connectionsError = $state<string | null>(null);
	let usableConnections = $derived(connections.filter((c) => USABLE_CONNECTION_TYPES.has(c.type)));

	// Live model dropdown (PRD follow-up: a free-typed model string had no validation at
	// all - "openai/openai/gpt-4o-mini" went uncaught until a real run hung). Never a
	// hardcoded list - fetched fresh per connection via GET /connections/{id}/models,
	// which makes a real client.models.list() call (app/main_runtime.py:listConnectionModels).
	type ModelProbe = { options: string[]; loading: boolean; error: string | null; requestId: number };
	const blankModelProbe = (): ModelProbe => ({ options: [], loading: false, error: null, requestId: 0 });
	let primaryModelProbe = $state<ModelProbe>(blankModelProbe());
	let fallbackModelProbe = $state<ModelProbe>(blankModelProbe());
	let modelProbeRequestCounter = 0;

	// `requestId` guards against a stale response overwriting a fresher one - e.g. the user
	// picks connection A then quickly switches to connection B before A's /models call
	// returns; without this, A's response could land after B's and leave B's connection
	// paired with A's model list in the dropdown.
	async function fetchModelsFor(connectionId: string, probe: ModelProbe) {
		const requestId = ++modelProbeRequestCounter;
		probe.requestId = requestId;
		probe.loading = true;
		probe.error = null;
		try {
			const result = await listConnectionModels(connectionId);
			if (probe.requestId !== requestId) return;
			probe.options = result.ok ? result.models : [];
			probe.error = result.ok ? null : (result.detail ?? 'Could not load the live model list.');
		} catch (e) {
			if (probe.requestId !== requestId) return;
			probe.options = [];
			probe.error = e instanceof Error ? e.message : 'Could not load the live model list.';
		} finally {
			if (probe.requestId === requestId) probe.loading = false;
		}
	}

	let lastFetchedConnectionId: string | null = null;
	$effect(() => {
		const id = model.connection_id;
		if (readonly) return;
		if (!id) {
			if (lastFetchedConnectionId !== null) {
				lastFetchedConnectionId = null;
				primaryModelProbe = blankModelProbe();
			}
			return;
		}
		if (id === lastFetchedConnectionId) return;
		lastFetchedConnectionId = id;
		fetchModelsFor(id, primaryModelProbe);
	});

	let lastFetchedFallbackConnectionId: string | null = null;
	$effect(() => {
		const id = model.fallback_connection_id;
		if (readonly) return;
		if (!id) {
			if (lastFetchedFallbackConnectionId !== null) {
				lastFetchedFallbackConnectionId = null;
				fallbackModelProbe = blankModelProbe();
			}
			return;
		}
		if (id === lastFetchedFallbackConnectionId) return;
		lastFetchedFallbackConnectionId = id;
		fetchModelsFor(id, fallbackModelProbe);
	});

	// Model Test button (complements the dropdown - a model can be listed by the provider
	// but not actually usable for chat completions) - same testResults-keyed-by-id /
	// 'testing' / {ok, detail} shape as connections/+page.svelte's own Test button.
	let modelTestResults = $state<Record<'primary' | 'fallback', { ok: boolean; detail: string | null } | 'testing'>>(
		{} as Record<'primary' | 'fallback', { ok: boolean; detail: string | null } | 'testing'>
	);

	async function runModelTest(key: 'primary' | 'fallback', connectionId: string, modelName: string) {
		modelTestResults[key] = 'testing';
		try {
			modelTestResults[key] = await testConnectionModel(connectionId, modelName);
		} catch (e) {
			modelTestResults[key] = { ok: false, detail: e instanceof Error ? e.message : 'Test failed.' };
		}
	}

	let tools = $state<Tool[]>([]);
	let toolsError = $state<string | null>(null);

	function toggleTool(toolId: string, checked: boolean) {
		selectedToolIds = checked
			? [...selectedToolIds, toolId]
			: selectedToolIds.filter((id) => id !== toolId);
	}

	let skills = $state<Skill[]>([]);
	let skillsError = $state<string | null>(null);

	function toggleSkill(skillName: string, checked: boolean) {
		selectedSkillNames = checked
			? [...selectedSkillNames, skillName]
			: selectedSkillNames.filter((name) => name !== skillName);
	}

	let knowledgeBases = $state<KnowledgeBase[]>([]);
	let knowledgeBasesError = $state<string | null>(null);

	function toggleKnowledgeBase(kbId: string, checked: boolean) {
		selectedKnowledgeBaseIds = checked
			? [...selectedKnowledgeBaseIds, kbId]
			: selectedKnowledgeBaseIds.filter((id) => id !== kbId);
	}

	onMount(async () => {
		await Promise.all([
			listConnections()
				.then((result) => (connections = result))
				.catch((e) => {
					connectionsError = e instanceof Error ? e.message : 'Failed to load connections.';
				}),
			listTools()
				.then((result) => (tools = result))
				.catch((e) => {
					toolsError = e instanceof Error ? e.message : 'Failed to load tools.';
				}),
			listSkills()
				.then((result) => (skills = result))
				.catch((e) => {
					skillsError = e instanceof Error ? e.message : 'Failed to load skills.';
				}),
			listKnowledgeBases()
				.then((result) => (knowledgeBases = result))
				.catch((e) => {
					knowledgeBasesError = e instanceof Error ? e.message : 'Failed to load knowledge bases.';
				})
		]);
	});

	function slugify(value: string): string {
		return value
			.toLowerCase()
			.trim()
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/(^-|-$)/g, '');
	}

	function onDisplayNameInput(event: Event) {
		identity.display_name = (event.currentTarget as HTMLInputElement).value;
		if (!identity.name || identity.name === slugify(identity.display_name)) {
			identity.name = slugify(identity.display_name);
		}
	}
</script>

<div class="grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr]">
	<nav aria-label="Agent editor tabs" class="flex flex-row gap-1 overflow-x-auto lg:flex-col">
		{#each tabs as tab (tab.id)}
			<button
				type="button"
				class="rounded-md px-3 py-2 text-left text-sm whitespace-nowrap
					{activeTab === tab.id ? 'bg-primary/10 font-medium text-primary-700' : 'text-neutral-600 hover:bg-neutral-100'}
					{!tab.enabled ? 'opacity-60' : ''}"
				aria-current={activeTab === tab.id ? 'page' : undefined}
				aria-disabled={!tab.enabled}
				onclick={() => (activeTab = tab.id)}
			>
				{tab.label}
				{#if !tab.enabled}
					<span class="ml-1 text-xs text-neutral-400">(phase)</span>
				{/if}
			</button>
		{/each}
	</nav>

	<div class="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
		{#if activeTab === 'identity'}
			<form class="space-y-5" onsubmit={(e) => e.preventDefault()} inert={readonly}>
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Display name</span>
						<input
							type="text"
							value={identity.display_name}
							oninput={onDisplayNameInput}
							placeholder="Vending Helper"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Name (slug)</span>
						<input
							type="text"
							bind:value={identity.name}
							placeholder="vending-support"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Owner</span>
						<input
							type="text"
							bind:value={identity.owner}
							placeholder="CS team"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Languages (comma-separated)</span>
						<input
							type="text"
							bind:value={languagesInput}
							placeholder="th, en"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
				</div>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Role (one sentence)</span>
					<input
						type="text"
						bind:value={identity.role}
						placeholder="Answers customer questions about using machines and refunds"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Persona</span>
					<input
						type="text"
						bind:value={identity.persona}
						placeholder="Polite, concise"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Tags (comma-separated)</span>
					<input
						type="text"
						bind:value={tagsInput}
						placeholder="support, vending"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Responsibilities</span>
					<textarea
						rows="3"
						bind:value={identity.responsibilities}
						placeholder="Check order status, explain refund steps, recommend products"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					></textarea>
				</label>

				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">In scope</span>
						<textarea
							rows="3"
							bind:value={identity.in_scope}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						></textarea>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Out of scope</span>
						<textarea
							rows="3"
							bind:value={identity.out_of_scope}
							placeholder="No topics outside the vending business; never grants discounts"
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						></textarea>
					</label>
				</div>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Handoff (condition → channel)</span>
					<input
						type="text"
						bind:value={identity.handoff}
						placeholder="Refund above 500 THB → call center"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Instructions (Markdown, optional)</span>
					<textarea
						rows="4"
						bind:value={identity.instructions}
						placeholder="Procedures, sample replies…"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					></textarea>
				</label>

				<p class="text-xs text-neutral-500">
					The compiler assembles this identity into the agent's system prompt from a shared
					template, including instructions to decline <code>out_of_scope</code> requests and follow
					<code>handoff</code> (PRD §6.1a).
				</p>
			</form>
		{:else if activeTab === 'model'}
			<form class="space-y-5" onsubmit={(e) => e.preventDefault()} inert={readonly}>
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Connection</span>
					{#if connectionsError}
						<div class="mt-1 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
							{connectionsError}
						</div>
					{:else if connections.length > 0 && usableConnections.length === 0}
						<p class="mt-1 text-sm text-neutral-500">
							No OpenAI-compatible connections found.
							<a href="/connections" class="text-primary underline">Create a connection</a> first.
						</p>
					{:else}
						<select
							bind:value={model.connection_id}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						>
							<option value="" disabled>Select a connection</option>
							{#each usableConnections as connection (connection.id)}
								<option value={connection.id}>{connection.name}</option>
							{/each}
						</select>
					{/if}
				</label>

				{@render modelPicker('model', 'connection_id', primaryModelProbe, 'primary', 'Model')}

				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Temperature (optional)</span>
						<input
							type="number"
							step="0.1"
							min="0"
							max="2"
							bind:value={model.temperature}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Max tokens (optional)</span>
						<input
							type="number"
							step="1"
							min="1"
							bind:value={model.max_tokens}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						/>
					</label>
				</div>

				<p class="text-xs text-neutral-500">
					Only connections the compiler can build an LLM from today (OpenAI-compatible) are listed
					(PRD §6.1).
				</p>

				<div class="border-t border-neutral-200 pt-5">
					<h3 class="text-sm font-medium text-neutral-700">Fallback model (optional)</h3>
					<p class="mt-1 text-xs text-neutral-500">
						If the primary provider returns a 429 or 5xx, the run retries twice automatically,
						then switches to this model if configured — the trace records which one answered.
					</p>

					<label class="mt-3 block">
						<span class="text-sm font-medium text-neutral-700">Fallback connection</span>
						<select
							bind:value={model.fallback_connection_id}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
								focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
						>
							<option value="">No fallback</option>
							{#each usableConnections as connection (connection.id)}
								<option value={connection.id}>{connection.name}</option>
							{/each}
						</select>
					</label>

					<div class="mt-3">
						{@render modelPicker(
							'fallback_model',
							'fallback_connection_id',
							fallbackModelProbe,
							'fallback',
							'Fallback model'
						)}
					</div>
				</div>
			</form>
		{:else if activeTab === 'tools'}
			<div class="space-y-4" inert={readonly}>
				{#if toolsError}
					<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
						{toolsError}
					</div>
				{:else if tools.length === 0}
					<p class="text-sm text-neutral-500">
						No tools yet. <a href="/tools" class="text-primary underline">Create a tool</a> first.
					</p>
				{:else}
					<ul class="divide-y divide-neutral-200 rounded-md border border-neutral-200">
						{#each tools as tool (tool.id)}
							{@const usable = isToolUsable(tool)}
							<li class="flex items-start gap-3 p-3">
								<input
									type="checkbox"
									id={`tool-${tool.id}`}
									class="mt-1"
									disabled={!usable}
									checked={selectedToolIds.includes(tool.id)}
									onchange={(e) => toggleTool(tool.id, (e.currentTarget as HTMLInputElement).checked)}
								/>
								<label for={`tool-${tool.id}`} class="flex-1 {usable ? '' : 'opacity-60'}">
									<p class="text-sm font-medium text-neutral-900">{tool.name}</p>
									<p class="text-xs text-neutral-500">
										kind: <code>{tool.kind}</code>
										{#if !tool.enabled}· disabled{/if}
										{#if !USABLE_TOOL_KINDS.has(tool.kind)}· not supported by the compiler yet{/if}
										{#if tool.kind === 'http' && !tool.config?.url}· missing a URL{/if}
										{#if tool.kind === 'builtin' && !tool.config?.provider}· missing a provider{/if}
									</p>
								</label>
							</li>
						{/each}
					</ul>
				{/if}

				<p class="text-xs text-neutral-500">
					Only enabled <code>http</code> tools can be attached today (PRD §6.5) — other kinds
					fail compilation until tool-secret storage and an execution story exist for them.
					Manage tools on the <a href="/tools" class="text-primary underline">Tools</a> page.
				</p>
			</div>
		{:else if activeTab === 'knowledge'}
			<div class="space-y-4" inert={readonly}>
				{#if knowledgeBasesError}
					<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
						{knowledgeBasesError}
					</div>
				{:else if knowledgeBases.length === 0}
					<p class="text-sm text-neutral-500">
						No knowledge bases yet. <a href="/knowledge-bases" class="text-primary underline">Create a knowledge base</a> first.
					</p>
				{:else}
					<ul class="divide-y divide-neutral-200 rounded-md border border-neutral-200">
						{#each knowledgeBases as kb (kb.id)}
							{@const usable = isKnowledgeBaseUsable(kb)}
							<li class="flex items-start gap-3 p-3">
								<input
									type="checkbox"
									id={`kb-${kb.id}`}
									class="mt-1"
									disabled={!usable}
									checked={selectedKnowledgeBaseIds.includes(kb.id)}
									onchange={(e) => toggleKnowledgeBase(kb.id, (e.currentTarget as HTMLInputElement).checked)}
								/>
								<label for={`kb-${kb.id}`} class="flex-1 {usable ? '' : 'opacity-60'}">
									<p class="text-sm font-medium text-neutral-900">{kb.name}</p>
									<p class="text-xs text-neutral-500">
										retrieval: <code>{kb.retrieval_mode}</code>
										{#if !usable}· hybrid retrieval isn't supported as an agent tool yet{/if}
									</p>
								</label>
							</li>
						{/each}
					</ul>
				{/if}

				<p class="text-xs text-neutral-500">
					Only vector-mode knowledge bases can be attached today (PRD §6.6) — the compiler
					builds a real retrieval tool from each one at publish time, which also requires at
					least one document to have finished indexing into it.
					Manage knowledge bases on the <a href="/knowledge-bases" class="text-primary underline">Knowledge</a> page.
				</p>
			</div>
		{:else if activeTab === 'skills'}
			<div class="space-y-4" inert={readonly}>
				{#if skillsError}
					<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
						{skillsError}
					</div>
				{:else if skills.length === 0}
					<p class="text-sm text-neutral-500">
						No skills yet. <a href="/skills" class="text-primary underline">Create a skill</a> first.
					</p>
				{:else}
					<ul class="divide-y divide-neutral-200 rounded-md border border-neutral-200">
						{#each skills as skill (skill.id)}
							{@const usable = isSkillUsable(skill)}
							<li class="flex items-start gap-3 p-3">
								<input
									type="checkbox"
									id={`skill-${skill.id}`}
									class="mt-1"
									disabled={!usable}
									checked={selectedSkillNames.includes(skill.name)}
									onchange={(e) => toggleSkill(skill.name, (e.currentTarget as HTMLInputElement).checked)}
								/>
								<label for={`skill-${skill.id}`} class="flex-1 {usable ? '' : 'opacity-60'}">
									<p class="text-sm font-medium text-neutral-900">{skill.name}</p>
									<p class="text-xs text-neutral-500">
										{#if skill.description}{skill.description} ·{/if}
										{skill.latest_version.is_published ? 'Published' : 'Draft (not attachable until published)'}
									</p>
								</label>
							</li>
						{/each}
					</ul>
				{/if}

				<p class="text-xs text-neutral-500">
					Only published skills can be attached today (PRD §6.6a) — the compiler checks the
					skill name against this company's published skills at publish time.
					Manage skills on the <a href="/skills" class="text-primary underline">Skills</a> page.
				</p>
			</div>
		{:else if activeTab === 'memory'}
			<form class="space-y-5" onsubmit={(e) => e.preventDefault()} inert={readonly}>
				<label class="flex items-center gap-2">
					<input type="checkbox" bind:checked={memory.enabled} class="rounded border-neutral-300" />
					<span class="text-sm font-medium text-neutral-700">Enable conversation memory</span>
				</label>

				{#if memory.enabled}
					<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
						<label class="block">
							<span class="text-sm font-medium text-neutral-700">Message limit (optional)</span>
							<input
								type="number"
								step="1"
								min="1"
								bind:value={memory.message_limit}
								placeholder="Default"
								class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
									focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
							/>
						</label>
						<label class="block">
							<span class="text-sm font-medium text-neutral-700">Save mode</span>
							<select
								bind:value={memory.save_mode}
								class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
									focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
							>
								<option value="full">Full — includes tool calls and reasoning</option>
								<option value="input_output">Input/output only — user message and final reply</option>
							</select>
						</label>
					</div>
				{/if}

				<p class="text-xs text-neutral-500">
					Conversation memory (PRD §6.2) recalls earlier turns of the same conversation for this
					agent. Leave message limit blank to use Dynamiq's own default. Input/output-only save
					mode keeps less context per turn but is cheaper and skips intermediate tool-call/reasoning
					detail.
				</p>
			</form>
		{:else if activeTab === 'publish_gate'}
			<form class="space-y-5" onsubmit={(e) => e.preventDefault()} inert={readonly}>
				<label class="flex items-center gap-2">
					<input type="checkbox" bind:checked={publishGate.enabled} class="rounded border-neutral-300" />
					<span class="text-sm font-medium text-neutral-700">Require a passing eval before publishing</span>
				</label>

				{#if publishGate.enabled}
					<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
						<label class="block">
							<span class="text-sm font-medium text-neutral-700">Metric</span>
							<select
								bind:value={publishGate.metric}
								class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
									focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
							>
								<option value="" disabled>Select a metric</option>
								{#each GATE_METRICS as metric (metric.name)}
									<option value={metric.name}>{metric.label}</option>
								{/each}
							</select>
						</label>
						<label class="block">
							<span class="text-sm font-medium text-neutral-700">Minimum score</span>
							<input
								type="number"
								step="0.01"
								min="0"
								bind:value={publishGate.threshold}
								placeholder="e.g. 0.8"
								class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
									focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
							/>
						</label>
					</div>
				{/if}

				<p class="text-xs text-neutral-500">
					Publish gate (PRD §6.7) - when enabled, publishing is blocked unless the most recent
					completed <a href="/evaluation" class="text-primary underline">eval run</a> for this
					version scored at least this threshold on the chosen metric. No eval run yet, or the
					metric wasn't scored in it, also blocks publishing. There is no override.
				</p>
			</form>
		{:else if activeTab === 'guardrails'}
			<div class="space-y-6" inert={readonly}>
				<div>
					<h3 class="text-sm font-semibold text-neutral-900">Input checks</h3>
					<p class="mb-2 text-xs text-neutral-500">Run against the user's message before the agent sees it.</p>
					{@render guardrailCheckList(guardrails.input, INPUT_CHECK_TYPES, 'input')}
				</div>
				<div>
					<h3 class="text-sm font-semibold text-neutral-900">Output checks</h3>
					<p class="mb-2 text-xs text-neutral-500">Run against the agent's reply before it reaches the user.</p>
					{@render guardrailCheckList(guardrails.output, ALL_CHECK_TYPES, 'output')}
				</div>

				<p class="text-xs text-neutral-500">
					<code>valid_json</code>/<code>valid_choices</code> only make sense on output (PRD §6.4) -
					they're not offered for input checks. <code>mask</code> is unavailable for checks with
					nothing identifiable to redact (<code>max_length</code>, <code>valid_json</code>,
					<code>valid_choices</code>).
				</p>
			</div>
		{:else if activeTab === 'advanced'}
			<div class="space-y-3">
				{#if !isAdmin}
					<div class="rounded-md border border-dashed border-neutral-300 p-8 text-center">
						<p class="text-sm font-medium text-neutral-600">Admin-only</p>
						<p class="mt-1 text-sm text-neutral-400">Ask a company admin to view the compiled definition.</p>
					</div>
				{:else if !compiledDefinition}
					<div class="rounded-md border border-dashed border-neutral-300 p-8 text-center">
						<p class="text-sm font-medium text-neutral-600">Not compiled yet</p>
						<p class="mt-1 text-sm text-neutral-400">
							compiled_definition is generated when a version is published (PRD §6.1) - this
							version hasn't been published yet.
						</p>
					</div>
				{:else}
					<p class="text-xs text-neutral-500">
						A snapshot taken at publish time - may not reflect the live compiled definition if
						the compiler or a referenced connection/tool/skill has changed since.
					</p>
					<pre class="overflow-auto rounded-md bg-neutral-900 p-4 text-xs text-neutral-100">{JSON.stringify(
							compiledDefinition,
							null,
							2
						)}</pre>
				{/if}
			</div>
		{:else}
			{@const tab = tabs.find((t) => t.id === activeTab)}
			<div class="rounded-md border border-dashed border-neutral-300 p-8 text-center">
				<p class="text-sm font-medium text-neutral-600">{tab?.label} is not part of this skeleton yet</p>
				{#if tab?.note}
					<p class="mt-1 text-sm text-neutral-400">{tab.note}</p>
				{/if}
			</div>
		{/if}
	</div>
</div>

{#snippet modelPicker(
	fieldKey: 'model' | 'fallback_model',
	connectionIdKey: 'connection_id' | 'fallback_connection_id',
	probe: ModelProbe,
	testKey: 'primary' | 'fallback',
	label: string
)}
	<label class="block">
		<span class="text-sm font-medium text-neutral-700">{label}</span>
		{#if probe.loading}
			<p class="mt-1 text-xs text-neutral-500">Loading available models…</p>
		{:else if probe.error}
			<p class="mt-1 text-xs text-neutral-500">
				Could not load the live model list ({probe.error}) — type the model name manually.
			</p>
		{:else if probe.options.length > 0}
			<p class="mt-1 text-xs text-neutral-500">
				Start typing to search {probe.options.length} available models, or enter one manually.
			</p>
		{/if}
		<div class="mt-1 flex gap-2">
			<input
				type="text"
				list={probe.options.length > 0 ? `${testKey}-model-options` : undefined}
				bind:value={model[fieldKey]}
				placeholder="gpt-4o-mini"
				autocomplete="off"
				class="w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
			{#if probe.options.length > 0}
				<datalist id="{testKey}-model-options">
					{#each probe.options as m (m)}
						<option value={m}></option>
					{/each}
				</datalist>
			{/if}
			<button
				type="button"
				disabled={!model[connectionIdKey] || !model[fieldKey].trim() || modelTestResults[testKey] === 'testing'}
				onclick={() => runModelTest(testKey, model[connectionIdKey], model[fieldKey].trim())}
				class="shrink-0 rounded-md border border-neutral-300 px-3 py-2 text-sm hover:bg-neutral-50
					disabled:opacity-50"
			>
				{modelTestResults[testKey] === 'testing' ? 'Testing…' : 'Test'}
			</button>
		</div>
		{#if modelTestResults[testKey] && modelTestResults[testKey] !== 'testing'}
			{@const result = modelTestResults[testKey] as { ok: boolean; detail: string | null }}
			<p class="mt-1 text-xs {result.ok ? 'text-success' : 'text-danger'}">
				{result.ok ? '✓' : '✗'} {result.detail ?? (result.ok ? 'Works.' : 'Failed.')}
			</p>
		{/if}
	</label>
{/snippet}

{#snippet guardrailCheckList(list: GuardrailCheckRow[], allowedTypes: GuardrailCheckType[], sectionLabel: string)}
	<div class="space-y-3">
		{#each list as check, i (check)}
			{@const noMask = NO_MASK_CHECK_TYPES.has(check.check_type)}
			<div class="space-y-2 rounded-md border border-neutral-200 p-3">
				<div class="flex items-start justify-between gap-2">
					<div class="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-3">
						<label class="block">
							<span class="text-xs font-medium text-neutral-600">Check type</span>
							<select
								bind:value={check.check_type}
								onchange={(e) => {
									const newType = (e.currentTarget as HTMLSelectElement).value as GuardrailCheckType;
									resetTypeSpecificFields(check, newType);
								}}
								class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1.5 text-sm"
							>
								{#each allowedTypes as t (t)}
									<option value={t}>{t}</option>
								{/each}
							</select>
						</label>
						<label class="block">
							<span class="text-xs font-medium text-neutral-600">Action</span>
							<select bind:value={check.action} class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1.5 text-sm">
								<option value="block">block</option>
								<option value="flag">flag</option>
								<option value="mask" disabled={noMask}>mask{noMask ? ' (unsupported)' : ''}</option>
							</select>
						</label>
						<label class="block">
							<span class="text-xs font-medium text-neutral-600">On error</span>
							<select bind:value={check.on_error} class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1.5 text-sm">
								<option value="flag">flag</option>
								<option value="block">block</option>
							</select>
						</label>
					</div>
					<button
						type="button"
						class="shrink-0 rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-50"
						onclick={() => list.splice(i, 1)}
					>
						Remove
					</button>
				</div>

				{#if check.check_type === 'max_length'}
					<label class="block">
						<span class="text-xs font-medium text-neutral-600">Max length</span>
						<input
							type="number"
							min="1"
							step="1"
							bind:value={check.max_length}
							class="mt-1 w-full max-w-xs rounded-md border border-neutral-300 px-2 py-1.5 text-sm"
						/>
					</label>
				{:else if check.check_type === 'regex_blocklist'}
					<label class="block">
						<span class="text-xs font-medium text-neutral-600">Pattern (regex)</span>
						<input
							type="text"
							bind:value={check.pattern}
							placeholder="\b(badword)\b"
							class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1.5 font-mono text-sm"
						/>
					</label>
				{:else if check.check_type === 'valid_choices'}
					<label class="block">
						<span class="text-xs font-medium text-neutral-600">Choices (comma-separated)</span>
						<input
							type="text"
							bind:value={
								() => check.choicesText ?? (check.choices ?? []).join(', '),
								(v) => (check.choicesText = v)
							}
							placeholder="refund, exchange, decline"
							class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1.5 text-sm"
						/>
					</label>
				{/if}

				<label class="block">
					<span class="text-xs font-medium text-neutral-600">Fallback message (optional)</span>
					<input
						type="text"
						bind:value={check.fallback_message}
						class="mt-1 w-full rounded-md border border-neutral-300 px-2 py-1.5 text-sm"
					/>
				</label>
			</div>
		{/each}

		<button
			type="button"
			class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
			onclick={() => list.push(newGuardrailCheck(allowedTypes))}
		>
			+ Add {sectionLabel} check
		</button>
	</div>
{/snippet}
