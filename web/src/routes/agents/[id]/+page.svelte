<script lang="ts">
	import { goto } from '$app/navigation';
	import {
		updateAgentVersion,
		createAgentVersion,
		publishAgentVersion,
		listAgentVersions,
		cloneAgent,
		archiveAgent,
		unarchiveAgent
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { Agent, AgentVersion } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import AgentSpecTabs from '$lib/components/agents/AgentSpecTabs.svelte';
	import { formStateFromVersion, blankIdentity, blankModelSpec, blankGuardrails } from '$lib/components/agents/specDefaults';
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

	const blankForm = formStateFromVersion(null);
	let identity = $state(blankForm.identity);
	let model = $state(blankForm.model);
	let selectedToolIds = $state<string[]>([]);
	let selectedSkillNames = $state<string[]>([]);
	let guardrails = $state(blankForm.guardrails);

	function loadFrom(newAgent: Agent | null, newVersions: AgentVersion[]) {
		agent = newAgent;
		versions = newVersions;
		const latest = newVersions[0] ?? null;
		const form = formStateFromVersion(latest);
		identity = form.identity;
		model = form.model;
		selectedToolIds = form.selectedToolIds;
		selectedSkillNames = form.selectedSkillNames;
		guardrails = form.guardrails;
		versionId = latest && !latest.is_published ? latest.id : null;
		// Clear banners left over from whatever the page was previously showing (a prior
		// save/publish result, or - since Clone navigates within this same route/component
		// instance rather than remounting - the agent this page used to be displaying).
		error = null;
		publishErrors = [];
		savedNotice = false;
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

		return {
			spec: {
				identity,
				model: modelResult,
				tools: selectedToolIds.map((tool_id) => ({ tool_id })),
				skills: selectedSkillNames,
				guardrails: guardrailsResult
			}
		};
	}

	async function saveDraft() {
		if (!agent || !versionId) return;
		error = null;
		publishErrors = [];
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
		savedNotice = false;
		creatingVersion = true;
		try {
			const latest = versions[0] ?? null;
			const sourceSpec = latest?.spec ?? {
				identity: blankIdentity(),
				model: blankModelSpec(),
				tools: [],
				skills: [],
				guardrails: blankGuardrails()
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
</script>

{#snippet headerActions()}
	<div class="flex gap-2">
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
			bind:guardrails
			{readonly}
		/>
	{/key}
{/if}
