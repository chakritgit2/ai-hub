<script lang="ts">
	import { onMount } from 'svelte';
	import { listConnections, listTools, listSkills, listKnowledgeBases } from '$lib/api/console';
	import type {
		AgentIdentity,
		Connection,
		GuardrailCheckSpec,
		GuardrailCheckType,
		GuardrailsSpec,
		KnowledgeBase,
		ModelSpec,
		Skill,
		Tool
	} from '$lib/api/types';

	// Only connection types the compiler can actually build an LLM for today
	// (ai/app/integrations/dynamiq_adapter.py's _CONNECTION_BUILDERS/_LLM_TYPES) - anything
	// else fails at publish time with "Unsupported connection type", so there's no point
	// offering it here. Update this allowlist if the compiler gains another provider.
	const USABLE_CONNECTION_TYPES = new Set(['dynamiq.connections.OpenAI', 'openai']);

	// Only kind: "http" tools can actually be built into a real node today
	// (ai/app/services/compiler.py's _resolve_tools) - builtin/python references fail
	// compilation outright for every role, so there's no point offering them here.
	// _resolve_tools also rejects an http tool with no config.url, so that's checked too
	// (isToolUsable below), not just the kind.
	const USABLE_TOOL_KINDS = new Set(['http']);

	function isToolUsable(tool: Tool): boolean {
		return USABLE_TOOL_KINDS.has(tool.kind) && tool.enabled && !!tool.config?.url;
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
	};

	type TabId = 'identity' | 'model' | 'tools' | 'knowledge' | 'skills' | 'memory' | 'guardrails' | 'advanced';

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
		{ id: 'memory', label: 'Memory', enabled: false, note: 'PRD §6.2' },
		{ id: 'guardrails', label: 'Guardrails', enabled: true },
		{ id: 'advanced', label: 'Advanced', enabled: false, note: 'Read-only compiled_definition, admin-only raw JSON — PRD §6.1' }
	];

	let activeTab = $state<TabId>('identity');

	let {
		identity = $bindable(),
		model = $bindable(),
		selectedToolIds = $bindable(),
		selectedSkillNames = $bindable(),
		selectedKnowledgeBaseIds = $bindable(),
		guardrails = $bindable(),
		readonly = false
	}: {
		identity: AgentIdentity;
		model: ModelFormState;
		selectedToolIds: string[];
		selectedSkillNames: string[];
		selectedKnowledgeBaseIds: string[];
		guardrails: GuardrailsSpec;
		readonly?: boolean;
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
		return {
			connection_id: model.connection_id,
			model: model.model.trim(),
			...(!isUnset(model.temperature) ? { temperature: Number(model.temperature) } : {}),
			...(!isUnset(model.max_tokens) ? { max_tokens: Number(model.max_tokens) } : {})
		};
	}

	let connections = $state<Connection[]>([]);
	let connectionsError = $state<string | null>(null);
	let usableConnections = $derived(connections.filter((c) => USABLE_CONNECTION_TYPES.has(c.type)));

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

				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Model</span>
					<input
						type="text"
						bind:value={model.model}
						placeholder="gpt-4o-mini"
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					/>
				</label>

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
										{#if tool.kind !== 'http'}· not supported by the compiler yet{/if}
										{#if tool.kind === 'http' && !tool.config?.url}· missing a URL{/if}
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
