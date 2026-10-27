<script lang="ts">
	import { parse as parseYaml, stringify as stringifyYaml } from 'yaml';
	import {
		updateWorkflow,
		archiveWorkflow,
		unarchiveWorkflow,
		createWorkflowVersion,
		updateWorkflowVersion,
		publishWorkflowVersion,
		listWorkflowVersions
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { Workflow, WorkflowVersion } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { currentUser, roleForCompany } from '$lib/stores/auth';
	import { companyId } from '$lib/stores/company';
	import YamlEditor from '$lib/components/YamlEditor.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const STARTER_SPEC = {
		states: [
			{
				id: 'state1',
				label: 'First state',
				agent_version_id: '',
				next_states: []
			}
		],
		initial_state: 'state1',
		manager: {
			connection_id: '',
			model: ''
		},
		max_loops: 15
	};

	let workflow = $state<Workflow | null>(null);
	let versions = $state<WorkflowVersion[]>([]);
	// The one version this page lets the user actually edit - the latest, only while it's
	// still an unpublished draft (publish makes a version immutable, PRD §6.9) and only
	// while the workflow itself isn't archived. null means "nothing editable right now" -
	// the page offers "New version" instead.
	let versionId = $state<string | null>(null);
	let latestVersion = $derived(versions[0] ?? null);
	let isAdmin = $derived(roleForCompany($currentUser, $companyId) === 'admin');

	let specText = $state('');
	let nameForm = $state({ name: '', description: '' });

	function loadFrom(newWorkflow: Workflow | null, newVersions: WorkflowVersion[]) {
		workflow = newWorkflow;
		versions = newVersions;
		nameForm = { name: newWorkflow?.name ?? '', description: newWorkflow?.description ?? '' };
		const latest = newVersions[0] ?? null;
		versionId = latest && !latest.is_published ? latest.id : null;
		specText = stringifyYaml(latest ? latest.spec : STARTER_SPEC);
		error = null;
		publishErrors = [];
		savedNotice = false;
	}

	let loadedForWorkflowId = $state<string | undefined>(undefined);
	$effect(() => {
		if (data.workflow?.id !== loadedForWorkflowId) {
			loadedForWorkflowId = data.workflow?.id;
			loadFrom(data.workflow, data.versions);
		}
	});

	let saving = $state(false);
	let publishing = $state(false);
	let creatingVersion = $state(false);
	let savingSettings = $state(false);
	let archiving = $state(false);
	let error = $state<string | null>(null);
	let settingsError = $state<string | null>(null);
	let publishErrors = $state<{ path: string; message: string }[]>([]);
	let savedNotice = $state(false);

	$effect(() => {
		headerContent.set({ menu: 'Workflows', title: workflow?.name ?? 'Workflow' });
	});

	function parseSpec(): { spec: Record<string, unknown> } | { error: string } {
		try {
			const parsed: unknown = parseYaml(specText);
			if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
				return { error: 'Spec must be a YAML mapping (object).' };
			}
			return { spec: parsed as Record<string, unknown> };
		} catch (e) {
			return { error: e instanceof Error ? `Invalid YAML: ${e.message}` : 'Invalid YAML.' };
		}
	}

	async function saveDraft() {
		if (!workflow) return;
		error = null;
		publishErrors = [];
		savedNotice = false;

		const parsed = parseSpec();
		if ('error' in parsed) {
			error = parsed.error;
			return;
		}

		saving = true;
		try {
			if (versionId) {
				await updateWorkflowVersion(workflow.id, versionId, { spec: parsed.spec });
			} else {
				const version = await createWorkflowVersion(workflow.id, { spec: parsed.spec });
				versionId = version.id;
			}
			versions = await listWorkflowVersions(workflow.id);
			savedNotice = true;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to save the draft.';
		} finally {
			saving = false;
		}
	}

	async function publish() {
		if (!workflow) return;
		error = null;
		publishErrors = [];
		savedNotice = false;

		const parsed = parseSpec();
		if ('error' in parsed) {
			error = parsed.error;
			return;
		}

		publishing = true;
		try {
			// Persist whatever is currently on screen before publishing - otherwise Publish
			// would compile/publish whatever was last saved and silently discard any edits
			// made since the last Save.
			let vid = versionId;
			if (vid) {
				await updateWorkflowVersion(workflow.id, vid, { spec: parsed.spec });
			} else {
				const version = await createWorkflowVersion(workflow.id, { spec: parsed.spec });
				vid = version.id;
				// Persisted now so a retry after a compile failure below reuses this same
				// draft instead of creating a new orphaned version each time.
				versionId = vid;
			}
			await publishWorkflowVersion(workflow.id, vid);
			versions = await listWorkflowVersions(workflow.id);
			versionId = null; // the version just published is now immutable
		} catch (e) {
			if (e instanceof ApiError && e.status === 422 && e.body && typeof e.body === 'object' && 'errors' in e.body) {
				publishErrors = (e.body as { errors: { path: string; message: string }[] }).errors;
			} else {
				error = e instanceof Error ? e.message : 'Failed to publish.';
			}
		} finally {
			publishing = false;
		}
	}

	async function createNewVersion() {
		if (!workflow) return;
		error = null;
		creatingVersion = true;
		try {
			const base = latestVersion ? latestVersion.spec : STARTER_SPEC;
			const version = await createWorkflowVersion(workflow.id, { spec: base as Record<string, unknown> });
			versions = await listWorkflowVersions(workflow.id);
			versionId = version.id;
			specText = stringifyYaml(version.spec);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to create a new version.';
		} finally {
			creatingVersion = false;
		}
	}

	async function saveSettings() {
		if (!workflow) return;
		savingSettings = true;
		settingsError = null;
		try {
			workflow = await updateWorkflow(workflow.id, nameForm);
		} catch (e) {
			settingsError = e instanceof Error ? e.message : 'Failed to save settings.';
		} finally {
			savingSettings = false;
		}
	}

	async function toggleArchive() {
		if (!workflow) return;
		archiving = true;
		error = null;
		try {
			workflow =
				workflow.status === 'archived'
					? await unarchiveWorkflow(workflow.id)
					: await archiveWorkflow(workflow.id);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to update the workflow.';
		} finally {
			archiving = false;
		}
	}
</script>

{#if data.loadError}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this workflow</p>
		<p class="mt-1 text-sm text-neutral-700">{data.loadError}</p>
	</div>
{:else if !workflow}
	<p class="text-sm text-neutral-500">Workflow not found.</p>
{:else}
	<div class="space-y-6">
		{#if workflow.status === 'archived'}
			<div class="rounded-md border border-neutral-300 bg-neutral-50 p-3 text-sm text-neutral-600">
				This workflow is archived - unarchive it to create or edit versions.
			</div>
		{/if}

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Spec</h2>

			{#if error}
				<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>
			{/if}
			{#if publishErrors.length > 0}
				<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
					<p class="font-medium">Publish failed — compiling the spec reported these problems:</p>
					<ul class="mt-1 list-disc pl-5">
						{#each publishErrors as e (e.path)}
							<li><code>{e.path}</code>: {e.message}</li>
						{/each}
					</ul>
				</div>
			{/if}
			{#if savedNotice}
				<div class="mt-3 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-primary-700">
					Draft saved. You can keep editing, or Publish when ready.
				</div>
			{/if}

			{#if versionId === null && latestVersion}
				<div class="mt-3 rounded-md border border-neutral-300 bg-neutral-50 p-3 text-sm text-neutral-600">
					v{latestVersion.version_no} is published and immutable.
					<button type="button" class="ml-1 text-primary underline" onclick={createNewVersion} disabled={creatingVersion || workflow.status === 'archived'}>
						{creatingVersion ? 'Creating…' : 'Start a new version from it'}
					</button>
				</div>
			{/if}

			<p class="mt-3 text-xs text-neutral-500">
				States reference existing, published Agents by <code>agent_version_id</code>. A state
				with one entry in <code>next_states</code> always goes there next; two or more (no
				condition) let the workflow's manager LLM pick at run time; zero is automatically
				wired to the end of the workflow.
			</p>

			<YamlEditor
				bind:value={specText}
				readOnly={(versionId === null && latestVersion !== null) || workflow.status === 'archived'}
			/>

			<div class="mt-3 flex gap-2">
				<button
					type="button"
					class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
						text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
					onclick={saveDraft}
					disabled={saving || publishing || workflow.status === 'archived'}
				>
					{saving ? 'Saving…' : versionId ? 'Save changes' : 'Save draft'}
				</button>
				<button
					type="button"
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm
						hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50"
					onclick={publish}
					disabled={saving || publishing || workflow.status === 'archived'}
				>
					{publishing ? 'Publishing…' : 'Publish'}
				</button>
			</div>

			{#if isAdmin && latestVersion?.compiled_definition}
				<details class="mt-4">
					<summary class="cursor-pointer text-xs text-neutral-500">Advanced: compiled definition</summary>
					<pre class="mt-2 overflow-x-auto rounded-md bg-neutral-900 p-3 text-xs text-neutral-100">{JSON.stringify(
							latestVersion.compiled_definition,
							null,
							2
						)}</pre>
				</details>
			{/if}
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Versions</h2>
			{#if versions.length === 0}
				<p class="mt-2 text-sm text-neutral-500">No versions yet.</p>
			{:else}
				<ul class="mt-2 divide-y divide-neutral-100 text-sm">
					{#each versions as version (version.id)}
						<li class="flex items-center justify-between py-2">
							<a href="/workflows/{workflow.id}/versions/{version.id}" class="text-primary underline">
								v{version.version_no}
							</a>
							<span
								class="rounded-full px-2 py-0.5 text-xs font-medium
									{version.is_published ? 'bg-primary/10 text-primary-700' : 'bg-neutral-100 text-neutral-500'}"
							>
								{version.is_published ? 'published' : 'draft'}
							</span>
						</li>
					{/each}
				</ul>
			{/if}
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Settings</h2>
			{#if settingsError}
				<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{settingsError}</div>
			{/if}
			<div class="mt-3 grid gap-3 sm:grid-cols-2">
				<label class="block">
					<span class="text-xs text-neutral-500">Name</span>
					<input
						type="text"
						bind:value={nameForm.name}
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm"
					/>
				</label>
				<label class="block">
					<span class="text-xs text-neutral-500">Description</span>
					<input
						type="text"
						bind:value={nameForm.description}
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm"
					/>
				</label>
			</div>
			<div class="mt-4 flex items-center justify-between">
				<button
					type="button"
					disabled={savingSettings}
					onclick={saveSettings}
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600 disabled:opacity-50"
				>
					{savingSettings ? 'Saving…' : 'Save settings'}
				</button>
				<button
					type="button"
					disabled={archiving}
					onclick={toggleArchive}
					class="rounded-md border border-danger/40 px-4 py-2 text-sm text-danger hover:bg-danger/10 disabled:opacity-50"
				>
					{archiving ? 'Working…' : workflow.status === 'archived' ? 'Unarchive' : 'Archive'}
				</button>
			</div>
		</section>
	</div>
{/if}
