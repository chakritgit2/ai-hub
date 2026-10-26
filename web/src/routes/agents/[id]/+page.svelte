<script lang="ts">
	import { goto } from '$app/navigation';
	import {
		updateAgent,
		updateAgentVersion,
		createAgentVersion,
		publishAgentVersion,
		listAgentVersions,
		cloneAgent,
		archiveAgent,
		unarchiveAgent,
		exportAgent,
		importAgent
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { Agent, AgentVersion } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import AgentSpecTabs from '$lib/components/agents/AgentSpecTabs.svelte';
	import {
		formStateFromVersion,
		blankIdentity,
		blankModelSpec,
		blankGuardrails,
		blankMemorySpec,
		blankPublishGateSpec,
		formatPublishGateError
	} from '$lib/components/agents/specDefaults';
	import { currentUser, roleForCompany } from '$lib/stores/auth';
	import { companyId } from '$lib/stores/company';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Local, mutable copies - archive/unarchive/publish/clone-navigation update these; `data`
	// itself only changes when SvelteKit reruns the loader (navigation/invalidation), and a
	// Clone followed by goto() to this same route reuses this component instance rather than
	// remounting it, so these can't just be `$state(data.agent)` one-time seeds - the $effect
	// below resyncs them whenever `data.agent.id` actually changes to a different agent.
	let agent = $state<Agent | null>(null);
	let versions = $state<AgentVersion[]>([]);
	// The one version this page lets the user actually edit - the latest, only while it's
	// still an unpublished draft (publish makes a version immutable, PRD §6.1; see
	// AgentsController::updateAgentVersion's 409 version_is_published), and only while the
	// agent itself isn't archived. null means "nothing editable right now" and the page
	// renders read-only with a "Create new version" action (when not archived).
	let versionId = $state<string | null>(null);
	let readonly = $derived(versionId === null || agent?.status === 'archived');
	let latestVersion = $derived(versions[0] ?? null);
	let isAdmin = $derived(roleForCompany($currentUser, $companyId) === 'admin');

	const blankForm = formStateFromVersion(null);
	let identity = $state(blankForm.identity);
	let model = $state(blankForm.model);
	let selectedToolIds = $state<string[]>([]);
	let selectedSkillNames = $state<string[]>([]);
	let selectedKnowledgeBaseIds = $state<string[]>([]);
	let guardrails = $state(blankForm.guardrails);
	let memory = $state(blankForm.memory);
	let publishGate = $state(blankForm.publishGate);

	function loadFrom(newAgent: Agent | null, newVersions: AgentVersion[]) {
		agent = newAgent;
		versions = newVersions;
		const latest = newVersions[0] ?? null;
		const form = formStateFromVersion(latest);
		identity = form.identity;
		model = form.model;
		selectedToolIds = form.selectedToolIds;
		selectedSkillNames = form.selectedSkillNames;
		selectedKnowledgeBaseIds = form.selectedKnowledgeBaseIds;
		guardrails = form.guardrails;
		memory = form.memory;
		publishGate = form.publishGate;
		versionId = latest && !latest.is_published ? latest.id : null;
		// Clear banners left over from whatever the page was previously showing (a prior
		// save/publish result, or - since Clone navigates within this same route/component
		// instance rather than remounting - the agent this page used to be displaying).
		error = null;
		publishErrors = [];
		publishGateError = null;
		savedNotice = false;
		renaming = false;
		exportError = null;
		importError = null;
		importSuccess = false;
	}

	let loadedForAgentId = $state<string | undefined>(undefined);
	$effect(() => {
		if (data.agent?.id !== loadedForAgentId) {
			loadedForAgentId = data.agent?.id;
			loadFrom(data.agent, data.versions);
		}
	});

	let tabsRef = $state<ReturnType<typeof AgentSpecTabs>>();

	let saving = $state(false);
	let publishing = $state(false);
	let creatingVersion = $state(false);
	let cloning = $state(false);
	let archiving = $state(false);
	let error = $state<string | null>(null);
	let publishErrors = $state<{ path: string; message: string }[]>([]);
	let publishGateError = $state<string | null>(null);
	let savedNotice = $state(false);

	$effect(() => {
		headerContent.set({
			menu: 'Agents',
			title: agent?.display_name ?? agent?.name ?? 'Agent',
			actions: headerActions
		});
	});

	// Reads the in-editor form state into a spec, or returns an error message if the form
	// isn't valid yet. Shared by saveDraft() and publish() so publish always persists
	// whatever is currently on screen instead of whatever was last saved.
	function buildSpec(): { spec: Record<string, unknown> } | { error: string } {
		tabsRef?.syncIdentityArrays();

		const modelResult = tabsRef?.buildModelSpec();
		if (!modelResult || typeof modelResult === 'string') {
			return { error: modelResult ?? 'Model: please select a connection and enter a model name.' };
		}
		const guardrailsResult = tabsRef?.buildGuardrailsSpec();
		if (!guardrailsResult || typeof guardrailsResult === 'string') {
			return { error: guardrailsResult ?? 'Guardrails: something went wrong building the spec.' };
		}
		const memoryResult = tabsRef?.buildMemorySpec();
		if (!memoryResult || typeof memoryResult === 'string') {
			return { error: memoryResult ?? 'Memory: something went wrong building the spec.' };
		}
		const publishGateResult = tabsRef?.buildPublishGateSpec();
		if (!publishGateResult || typeof publishGateResult === 'string') {
			return { error: publishGateResult ?? 'Publish gate: something went wrong building the spec.' };
		}

		return {
			spec: {
				identity,
				model: modelResult,
				tools: selectedToolIds.map((tool_id) => ({ tool_id })),
				skills: selectedSkillNames,
				knowledge: selectedKnowledgeBaseIds.map((kb_id) => ({ kb_id })),
				guardrails: guardrailsResult,
				memory: memoryResult,
				publish_gate: publishGateResult
			}
		};
	}

	async function saveDraft() {
		if (!agent || !versionId) return;
		error = null;
		publishErrors = [];
		publishGateError = null;
		savedNotice = false;

		const built = buildSpec();
		if ('error' in built) {
			error = built.error;
			return;
		}

		saving = true;
		try {
			const updated = await updateAgentVersion(agent.id, versionId, { spec: built.spec });
			versions = versions.map((v) => (v.id === updated.id ? updated : v));
			savedNotice = true;
		} catch (e) {
			if (e instanceof ApiError && e.status === 409) {
				await refreshAfterConflict(
					'This draft was just published elsewhere and can no longer be edited - showing the published version.'
				);
			} else {
				error = e instanceof Error ? e.message : 'Failed to save changes.';
			}
		} finally {
			saving = false;
		}
	}

	// Re-fetches `versions` after a 409 (someone else published this draft concurrently) so
	// `versions`/`versionId`/the form fields reflect what the server actually has, instead of
	// this tab's now-stale local copy - otherwise a later createNewVersion() would carry
	// forward this tab's stale pre-conflict spec and silently discard the other publisher's
	// changes. `message` is applied after loadFrom() so the conflict banner survives
	// loadFrom()'s own banner reset instead of being wiped out immediately.
	async function refreshAfterConflict(message: string) {
		if (!agent) return;
		try {
			const fresh = await listAgentVersions(agent.id);
			loadFrom(agent, fresh);
		} catch {
			// Best-effort refresh - if even this fails, fall back to just going read-only
			// locally; the version list/editor will be correct again on next page load.
			versionId = null;
		}
		error = message;
	}

	async function publish() {
		if (!agent || !versionId) return;
		error = null;
		publishErrors = [];
		publishGateError = null;
		savedNotice = false;

		const built = buildSpec();
		if ('error' in built) {
			error = built.error;
			return;
		}

		publishing = true;
		try {
			// Persist whatever is currently on screen before publishing - otherwise Publish
			// would compile/publish whatever was last saved and silently discard any edits
			// made since (there's no dirty-check gating this button from Save changes).
			const updated = await updateAgentVersion(agent.id, versionId, { spec: built.spec });
			versions = versions.map((v) => (v.id === updated.id ? updated : v));

			const published = await publishAgentVersion(agent.id, versionId);
			versions = versions.map((v) => (v.id === published.id ? published : v));
			versionId = null;
		} catch (e) {
			if (e instanceof ApiError && e.status === 422 && e.body && typeof e.body === 'object' && 'errors' in e.body) {
				publishErrors = (e.body as { errors: { path: string; message: string }[] }).errors;
			} else if (
				e instanceof ApiError &&
				e.status === 400 &&
				e.body &&
				typeof e.body === 'object' &&
				(e.body as { error?: string }).error === 'publish_gate_failed'
			) {
				publishGateError = formatPublishGateError(
					e.body as { reason: string; metric?: string; threshold?: number; actual?: number }
				);
			} else if (e instanceof ApiError && e.status === 409) {
				await refreshAfterConflict('This version was already published elsewhere.');
			} else {
				error = e instanceof Error ? e.message : 'Failed to publish.';
			}
		} finally {
			publishing = false;
		}
	}

	// Starts a new draft version carrying forward the latest version's spec (or a blank one
	// if the agent has no versions at all) - mirrors cloneAgent's own "carry spec forward"
	// pattern, just scoped to this agent's own version history instead of across agents.
	async function createNewVersion() {
		if (!agent) return;
		error = null;
		publishErrors = [];
		publishGateError = null;
		savedNotice = false;
		creatingVersion = true;
		try {
			const latest = versions[0] ?? null;
			const sourceSpec = latest?.spec ?? {
				identity: blankIdentity(),
				model: blankModelSpec(),
				tools: [],
				skills: [],
				knowledge: [],
				guardrails: blankGuardrails(),
				memory: blankMemorySpec(),
				publish_gate: blankPublishGateSpec()
			};
			const version = await createAgentVersion(agent.id, { spec: sourceSpec });
			const newVersions = [version, ...versions];
			loadFrom(agent, newVersions);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to create a new version.';
		} finally {
			creatingVersion = false;
		}
	}

	async function doClone() {
		if (!agent) return;
		error = null;
		cloning = true;
		try {
			const cloned = await cloneAgent(agent.id);
			await goto(`/agents/${cloned.id}`);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to clone this agent.';
			cloning = false;
		}
	}

	async function doArchive() {
		if (!agent) return;
		if (!confirm(`Archive "${agent.display_name ?? agent.name}"? It's hidden from the catalog by default afterward - you can unarchive it from this page any time.`)) {
			return;
		}
		error = null;
		archiving = true;
		try {
			agent = await archiveAgent(agent.id);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to archive this agent.';
		} finally {
			archiving = false;
		}
	}

	async function doUnarchive() {
		if (!agent) return;
		error = null;
		archiving = true;
		try {
			agent = await unarchiveAgent(agent.id);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to unarchive this agent.';
		} finally {
			archiving = false;
		}
	}

	// Shared across rename/export/import below - every one of these endpoints can 403/404
	// the same way (membership revoked mid-session, agent deleted by someone else).
	const COMMON_ERROR_MESSAGES: Record<string, string> = {
		forbidden: 'You no longer have permission to do this.',
		not_found: 'This agent no longer exists.'
	};

	// ---- Rename / redescribe (console.agents.name/description - independent of the
	// identity.name/role carried inside each version's spec) ----
	let renaming = $state(false);
	let renameForm = $state({ name: '', description: '' });
	let renameError = $state<string | null>(null);
	let savingRename = $state(false);

	const RENAME_ERROR_MESSAGES: Record<string, string> = {
		...COMMON_ERROR_MESSAGES,
		name_required: 'Name is required.',
		name_already_exists: 'An agent with this name already exists.'
	};

	function openRename() {
		if (!agent) return;
		renameForm = { name: agent.name, description: agent.description ?? '' };
		renameError = null;
		renaming = true;
	}

	async function submitRename() {
		if (!agent) return;
		savingRename = true;
		renameError = null;
		try {
			agent = await updateAgent(agent.id, {
				name: renameForm.name,
				description: renameForm.description.trim() === '' ? null : renameForm.description
			});
			renaming = false;
		} catch (e) {
			renameError =
				e instanceof ApiError ? (RENAME_ERROR_MESSAGES[e.message] ?? e.message) : 'Failed to save changes.';
		} finally {
			savingRename = false;
		}
	}

	// ---- Export / import YAML (AgentsController::exportAgent/importAgent) ----
	let exporting = $state(false);
	let exportError = $state<string | null>(null);
	let importing = $state(false);
	let importError = $state<string | null>(null);
	let importSuccess = $state(false);
	let importFileInput = $state<HTMLInputElement>();

	async function doExport() {
		if (!agent) return;
		exportError = null;
		exporting = true;
		try {
			const yaml = await exportAgent(agent.id);
			const blob = new Blob([yaml], { type: 'application/yaml' });
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `${agent.name}.yaml`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (e) {
			exportError =
				e instanceof ApiError
					? e.message === 'no_versions'
						? 'This agent has no versions yet - nothing to export.'
						: (COMMON_ERROR_MESSAGES[e.message] ?? e.message)
					: 'Failed to export this agent.';
		} finally {
			exporting = false;
		}
	}

	function triggerImport() {
		importError = null;
		importSuccess = false;
		importFileInput?.click();
	}

	const IMPORT_ERROR_MESSAGES: Record<string, string> = {
		...COMMON_ERROR_MESSAGES,
		yaml_required: 'The selected file is empty.',
		invalid_yaml: 'Could not parse this file as YAML.',
		spec_required: 'This file has no "spec" section - is it an agent export?',
		agent_archived: 'This agent is archived - unarchive it first.',
		version_conflict_retry: 'Someone else imported or created a version at the same moment - try again.'
	};

	async function onImportFileChange(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file || !agent) return;

		const proceed = confirm(
			`Import "${file.name}" as a new draft version of "${agent.display_name ?? agent.name}"? This won't change the agent's name or description.`
		);
		if (!proceed) {
			input.value = '';
			return;
		}

		importing = true;
		importError = null;
		try {
			const yaml = await file.text();
			await importAgent(agent.id, yaml);
			const fresh = await listAgentVersions(agent.id);
			loadFrom(agent, fresh);
			importSuccess = true;
		} catch (e) {
			importError =
				e instanceof ApiError && e.message === 'yaml_too_large'
					? `File is too large (max ${(e.body as { max_bytes?: number } | undefined)?.max_bytes ?? '?'} bytes).`
					: e instanceof ApiError
						? (IMPORT_ERROR_MESSAGES[e.message] ?? e.message)
						: 'Failed to import this file.';
		} finally {
			importing = false;
			input.value = '';
		}
	}
</script>

{#snippet headerActions()}
	<div class="flex flex-wrap gap-2">
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50"
			onclick={openRename}
		>
			Rename
		</button>
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
			onclick={doExport}
			disabled={exporting}
		>
			{exporting ? 'Exporting…' : 'Export YAML'}
		</button>
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
			onclick={triggerImport}
			disabled={importing || agent?.status === 'archived'}
			title={agent?.status === 'archived' ? 'Unarchive this agent first' : 'Import a YAML export as a new draft version'}
		>
			{importing ? 'Importing…' : 'Import YAML'}
		</button>
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
			onclick={doClone}
			disabled={cloning}
		>
			{cloning ? 'Cloning…' : 'Clone'}
		</button>
		{#if agent?.status === 'archived'}
			<button
				type="button"
				class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
					text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
				onclick={doUnarchive}
				disabled={archiving}
			>
				{archiving ? 'Unarchiving…' : 'Unarchive'}
			</button>
		{:else}
			<button
				type="button"
				class="rounded-md border border-danger/40 bg-white px-4 py-2 text-sm font-medium
					text-danger shadow-sm hover:bg-danger/5 disabled:opacity-50"
				onclick={doArchive}
				disabled={archiving}
			>
				{archiving ? 'Archiving…' : 'Archive'}
			</button>
		{/if}

		{#if agent?.status === 'archived'}
			<!-- Unarchive first to resume editing or start a new version. -->
		{:else if versionId}
			<button
				type="button"
				class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
					text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
				onclick={saveDraft}
				disabled={saving || publishing}
			>
				{saving ? 'Saving…' : 'Save changes'}
			</button>
			<button
				type="button"
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm
					hover:bg-primary-600 disabled:cursor-not-allowed disabled:bg-neutral-200 disabled:text-neutral-500"
				onclick={publish}
				disabled={saving || publishing}
			>
				{publishing ? 'Publishing…' : 'Publish'}
			</button>
		{:else}
			<button
				type="button"
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm
					hover:bg-primary-600 disabled:opacity-50"
				onclick={createNewVersion}
				disabled={creatingVersion}
				title="Published versions are immutable - edit a new draft instead"
			>
				{creatingVersion ? 'Creating…' : 'Create new version'}
			</button>
		{/if}
	</div>
{/snippet}

<input
	bind:this={importFileInput}
	type="file"
	accept=".yaml,.yml,application/yaml,text/yaml"
	class="hidden"
	onchange={onImportFileChange}
/>

{#if data.loadError}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this agent</p>
		<p class="mt-1 text-sm text-neutral-700">{data.loadError}</p>
	</div>
{:else if !agent}
	<p class="text-sm text-neutral-500">Agent not found.</p>
{:else}
	{#if error}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
			{error}
		</div>
	{/if}

	{#if exportError}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
			{exportError}
		</div>
	{/if}

	{#if importError}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
			{importError}
		</div>
	{/if}

	{#if importSuccess}
		<div class="mb-4 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-primary-700">
			Imported as a new draft version below.
		</div>
	{/if}

	{#if renaming}
		<form
			class="mb-4 grid grid-cols-1 gap-3 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2"
			onsubmit={(e) => {
				e.preventDefault();
				submitRename();
			}}
		>
			{#if renameError}
				<p class="text-sm text-danger sm:col-span-2">{renameError}</p>
			{/if}
			<label class="block">
				<span class="text-sm font-medium text-neutral-700">Name</span>
				<input
					type="text"
					bind:value={renameForm.name}
					required
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				/>
			</label>
			<label class="block">
				<span class="text-sm font-medium text-neutral-700">Description</span>
				<input
					type="text"
					bind:value={renameForm.description}
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				/>
			</label>
			<div class="flex gap-2 sm:col-span-2">
				<button
					type="submit"
					disabled={savingRename}
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
				>
					{savingRename ? 'Saving…' : 'Save'}
				</button>
				<button
					type="button"
					class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
					onclick={() => (renaming = false)}
				>
					Cancel
				</button>
			</div>
		</form>
	{/if}

	{#if publishErrors.length > 0}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
			<p class="font-medium">Publish failed — compiling the spec reported these problems:</p>
			<ul class="mt-1 list-disc pl-5">
				{#each publishErrors as e (e.path)}
					<li><code>{e.path}</code>: {e.message}</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if publishGateError}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
			<p class="font-medium">Publish blocked by the publish gate</p>
			<p class="mt-1">{publishGateError}</p>
		</div>
	{/if}

	{#if savedNotice}
		<div class="mb-4 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-primary-700">
			Changes saved.
		</div>
	{/if}

	{#if agent.status === 'archived'}
		<div class="mb-4 rounded-md border border-neutral-300 bg-neutral-50 p-3 text-sm text-neutral-600">
			This agent is archived.
		</div>
	{/if}

	{#if readonly}
		<div class="mb-4 rounded-md border border-neutral-300 bg-neutral-50 p-3 text-sm text-neutral-600">
			{#if agent.status === 'archived'}
				Read-only while archived — unarchive to resume editing.
			{:else if latestVersion?.is_published}
				Showing published version {latestVersion.version_no} (read-only) — published versions are
				immutable. Click "Create new version" to start editing.
			{:else}
				This agent has no versions yet. Click "Create new version" to start one.
			{/if}
		</div>
	{/if}

	{#if versions.length > 0}
		<details class="mb-4 rounded-md border border-neutral-200 bg-white text-sm">
			<summary class="cursor-pointer select-none px-3 py-2 font-medium text-neutral-700">
				Versions ({versions.length})
			</summary>
			<ul class="divide-y divide-neutral-100 border-t border-neutral-200">
				{#each versions as v (v.id)}
					<li class="flex items-center justify-between px-3 py-2 text-neutral-600">
						<a href="/agents/{agent.id}/versions/{v.id}" class="text-primary underline">v{v.version_no}</a>
						<span>
							{v.is_published ? 'Published' : 'Draft'}
							{#if v.id === versionId}· editing{/if}
						</span>
					</li>
				{/each}
			</ul>
		</details>
	{/if}

	{#key agent.id}
		<AgentSpecTabs
			bind:this={tabsRef}
			bind:identity
			bind:model
			bind:selectedToolIds
			bind:selectedSkillNames
			bind:selectedKnowledgeBaseIds
			bind:guardrails
			bind:memory
			bind:publishGate
			{readonly}
			{isAdmin}
			compiledDefinition={latestVersion?.compiled_definition ?? null}
		/>
	{/key}
{/if}
