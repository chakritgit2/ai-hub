<script lang="ts">
	import { goto } from '$app/navigation';
	import { createAgentVersion, listAgentVersions } from '$lib/api/console';
	import { headerContent } from '$lib/stores/header';
	import AgentSpecTabs from '$lib/components/agents/AgentSpecTabs.svelte';
	import { formStateFromVersion } from '$lib/components/agents/specDefaults';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Read-only view, so these never change after the initial seed from `data.version` - no
	// reactivity concern the way agents/[id]/+page.svelte has to manage for its live editor.
	const form = formStateFromVersion(data.version);
	let identity = $state(form.identity);
	let model = $state(form.model);
	let selectedToolIds = $state(form.selectedToolIds);
	let selectedSkillNames = $state(form.selectedSkillNames);
	let guardrails = $state(form.guardrails);

	// `versions[0]` is the agent's current latest (already sorted version_no DESC by the
	// backend) - if it's an unpublished draft other than the one being viewed here, restoring
	// would create yet another version on top of it, making that in-progress draft no longer
	// "latest" and so no longer reachable as editable from agents/[id] (it stays viewable via
	// its own version-detail page, just not lost) - worth a confirmation, not a silent surprise.
	let currentDraft = $derived(
		data.versions[0] && !data.versions[0].is_published ? data.versions[0] : null
	);
	let restoringOverDraft = $derived(currentDraft !== null && currentDraft.id !== data.version?.id);

	let restoring = $state(false);
	let error = $state<string | null>(null);

	$effect(() => {
		headerContent.set({
			menu: 'Agents',
			title: data.version ? `v${data.version.version_no} — ${data.agent?.display_name ?? data.agent?.name ?? 'Agent'}` : 'Version',
			actions: headerActions
		});
	});

	async function restoreAsNewDraft() {
		if (!data.agent || !data.version) return;
		if (data.agent.status === 'archived') {
			error = 'This agent is archived - unarchive it first to create a new draft.';
			return;
		}
		error = null;
		restoring = true;
		try {
			// Re-fetch right before restoring rather than trusting `data.versions` (read once
			// at page load, possibly long since stale) - the confirm below should reflect
			// what's actually latest right now, not what it was when this page opened.
			const freshVersions = await listAgentVersions(data.agent.id);
			const freshCurrentDraft = freshVersions[0] && !freshVersions[0].is_published ? freshVersions[0] : null;
			if (
				freshCurrentDraft &&
				freshCurrentDraft.id !== data.version.id &&
				!confirm(
					`v${freshCurrentDraft.version_no} is currently an in-progress, unpublished draft. Restoring this version will start a new draft on top of it (v${freshCurrentDraft.version_no} stays viewable in Versions, but won't be the editable one anymore). Continue?`
				)
			) {
				restoring = false;
				return;
			}
			await createAgentVersion(data.agent.id, { spec: data.version.spec });
			await goto(`/agents/${data.agent.id}`);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to restore this version as a new draft.';
			restoring = false;
		}
	}
</script>

{#snippet headerActions()}
	<div class="flex gap-2">
		<a
			href={data.agent ? `/agents/${data.agent.id}` : '/agents'}
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
				text-neutral-700 shadow-sm hover:bg-neutral-50"
		>
			Back to agent
		</a>
		<button
			type="button"
			class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm
				hover:bg-primary-600 disabled:opacity-50"
			onclick={restoreAsNewDraft}
			disabled={restoring || !data.agent || !data.version || data.agent.status === 'archived'}
			title={data.agent?.status === 'archived' ? 'Unarchive this agent first' : undefined}
		>
			{restoring ? 'Restoring…' : 'Restore as new draft'}
		</button>
	</div>
{/snippet}

{#if data.loadError}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this version</p>
		<p class="mt-1 text-sm text-neutral-700">{data.loadError}</p>
	</div>
{:else if !data.version}
	<p class="text-sm text-neutral-500">Version not found.</p>
{:else}
	{#if error}
		<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
			{error}
		</div>
	{/if}

	<div class="mb-4 rounded-md border border-neutral-300 bg-neutral-50 p-3 text-sm text-neutral-600">
		Read-only — v{data.version.version_no} is {data.version.is_published ? 'published and immutable' : 'a past draft'}.
		"Restore as new draft" copies this spec into a fresh editable version.
		{#if restoringOverDraft}
			<strong class="block mt-1">v{currentDraft?.version_no} is currently in progress — restoring will supersede it as the editable version.</strong>
		{/if}
	</div>

	<AgentSpecTabs bind:identity bind:model bind:selectedToolIds bind:selectedSkillNames bind:guardrails readonly />
{/if}
