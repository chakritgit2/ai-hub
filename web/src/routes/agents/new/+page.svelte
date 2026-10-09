<script lang="ts">
	import { goto } from '$app/navigation';
	import { createAgent, createAgentVersion, updateAgentVersion, publishAgentVersion } from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import { headerContent } from '$lib/stores/header';
	import AgentSpecTabs from '$lib/components/agents/AgentSpecTabs.svelte';
	import { blankIdentity, blankModelFormState, blankGuardrails } from '$lib/components/agents/specDefaults';

	let identity = $state(blankIdentity());
	let model = $state(blankModelFormState());
	let selectedToolIds = $state<string[]>([]);
	let selectedSkillNames = $state<string[]>([]);
	let selectedKnowledgeBaseIds = $state<string[]>([]);
	let guardrails = $state(blankGuardrails());

	let tabsRef: ReturnType<typeof AgentSpecTabs> | undefined;

	// Set once Save draft's createAgent/createAgentVersion succeed - lets a retry after a
	// partial failure (e.g. agent created but version creation threw) skip re-creating
	// what already exists instead of leaving an orphaned agent row behind.
	let agentId = $state<string | null>(null);
	let versionId = $state<string | null>(null);
	let isPublished = $state(false);

	let saving = $state(false);
	let publishing = $state(false);
	let error = $state<string | null>(null);
	let publishErrors = $state<{ path: string; message: string }[]>([]);
	let savedNotice = $state(false);

	$effect(() => {
		headerContent.set({ menu: 'Agents', title: 'New agent', actions: headerActions });
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
				knowledge: selectedKnowledgeBaseIds.map((kb_id) => ({ kb_id })),
				guardrails: guardrailsResult
			}
		};
	}

	async function saveDraft() {
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
			if (!agentId) {
				const agent = await createAgent({
					name: identity.name,
					description: identity.role,
					status: 'draft'
				});
				agentId = agent.id;
			}

			if (!versionId) {
				const version = await createAgentVersion(agentId, { spec: built.spec });
				versionId = version.id;
			} else {
				await updateAgentVersion(agentId, versionId, { spec: built.spec });
			}
			savedNotice = true;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to save the draft agent.';
		} finally {
			saving = false;
		}
	}

	async function publish() {
		if (!agentId || !versionId || isPublished) return;
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
			// made since the last Save.
			await updateAgentVersion(agentId, versionId, { spec: built.spec });
			await publishAgentVersion(agentId, versionId);
			isPublished = true;
			await goto(`/agents/${agentId}`);
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
</script>

{#snippet headerActions()}
	<div class="flex gap-2">
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
			onclick={saveDraft}
			disabled={saving || publishing || isPublished}
		>
			{saving ? 'Saving…' : versionId ? 'Save changes' : 'Save draft'}
		</button>
		<button
			type="button"
			class="rounded-md px-4 py-2 text-sm font-medium shadow-sm disabled:cursor-not-allowed
				{versionId && !isPublished ? 'bg-primary text-white hover:bg-primary-600' : 'bg-neutral-200 text-neutral-500'}"
			onclick={publish}
			disabled={!versionId || isPublished || saving || publishing}
			title={versionId ? undefined : 'Publish is available once the draft is saved'}
		>
			{publishing ? 'Publishing…' : isPublished ? 'Published' : 'Publish'}
		</button>
	</div>
{/snippet}

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
		Draft saved. You can keep editing, or Publish when ready.
	</div>
{/if}

{#if isPublished}
	<div class="mb-4 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-primary-700">
		Published. Redirecting…
	</div>
{/if}

<AgentSpecTabs
	bind:this={tabsRef}
	bind:identity
	bind:model
	bind:selectedToolIds
	bind:selectedSkillNames
	bind:selectedKnowledgeBaseIds
	bind:guardrails
/>
