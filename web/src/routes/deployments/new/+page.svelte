<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		listAgents,
		listAgentVersions,
		listWorkflows,
		listWorkflowVersions,
		createDeployment
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { Agent, AgentVersion, Workflow, WorkflowVersion } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	headerContent.set({ menu: 'Deployments', title: 'New deployment' });

	// A deployment targets exactly one of a published agent version or a published
	// workflow version (PRD §6.3) - the create form only needs slug + that target +
	// environment; everything else (limits, CORS, guardrails, ...) uses the backend's own
	// defaults and is edited afterward from the deployment's own "Save changes" section,
	// same "create light, configure on the detail page" shape datasets/+page.svelte and
	// knowledge-bases/+page.svelte already use.
	type TargetType = 'agent' | 'workflow';

	let targetType = $state<TargetType>('agent');
	let slug = $state('');
	let environment = $state<'staging' | 'production'>('production');

	let agents = $state<Agent[]>([]);
	let agentVersions = $state<AgentVersion[]>([]);
	let selectedAgentId = $state('');
	let selectedAgentVersionId = $state('');

	let workflows = $state<Workflow[]>([]);
	let workflowVersions = $state<WorkflowVersion[]>([]);
	let selectedWorkflowId = $state('');
	let selectedWorkflowVersionId = $state('');

	let loadError = $state<string | null>(null);
	let submitting = $state(false);
	let formError = $state<string | null>(null);

	async function loadPickers() {
		loadError = null;
		try {
			[agents, workflows] = await Promise.all([listAgents(), listWorkflows()]);
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Failed to load agents/workflows.';
		}
	}

	onMount(loadPickers);
	// Same "retry once the company switcher corrects a stale placeholder company_id"
	// pattern every list/detail page uses - see project-company-id-race-backlog.
	$effect(() => {
		$companyId;
		loadPickers();
	});

	// Agent -> Agent Version cascade, same pattern evaluation/new/+page.svelte already uses.
	$effect(() => {
		const agentId = selectedAgentId;
		selectedAgentVersionId = '';
		agentVersions = [];
		if (!agentId) return;
		listAgentVersions(agentId)
			.then((versions) => {
				agentVersions = versions.filter((v) => v.is_published);
			})
			.catch((e) => {
				loadError = e instanceof Error ? e.message : 'Failed to load agent versions.';
			});
	});

	// Workflow -> Workflow Version cascade, same shape as the agent one above.
	$effect(() => {
		const workflowId = selectedWorkflowId;
		selectedWorkflowVersionId = '';
		workflowVersions = [];
		if (!workflowId) return;
		listWorkflowVersions(workflowId)
			.then((versions) => {
				workflowVersions = versions.filter((v) => v.is_published);
			})
			.catch((e) => {
				loadError = e instanceof Error ? e.message : 'Failed to load workflow versions.';
			});
	});

	// Switching target type must not leave a stale selection from the other type behind -
	// only one of agent_version_id/workflow_version_id may ever be sent.
	function selectTargetType(next: TargetType) {
		targetType = next;
		selectedAgentId = '';
		selectedAgentVersionId = '';
		selectedWorkflowId = '';
		selectedWorkflowVersionId = '';
	}

	let canSubmit = $derived(
		!!slug.trim() && (targetType === 'agent' ? !!selectedAgentVersionId : !!selectedWorkflowVersionId)
	);

	// The PHP controller's error codes (console-api/app/controllers/admin/
	// DeploymentsController.php) are plain strings like "slug_already_exists" - map the
	// ones a user is likely to actually hit into readable copy rather than showing the
	// raw code.
	const ERROR_MESSAGES: Record<string, string> = {
		slug_already_exists: 'This slug is already used by another deployment. Pick a different one.',
		agent_version_not_published: 'That agent version is not published yet - publish it first.',
		workflow_version_not_published: 'That workflow version is not published yet - publish it first.'
	};

	async function submit() {
		submitting = true;
		formError = null;
		try {
			const created = await createDeployment({
				slug: slug.trim(),
				environment,
				agent_version_id: targetType === 'agent' ? selectedAgentVersionId : undefined,
				workflow_version_id: targetType === 'workflow' ? selectedWorkflowVersionId : undefined
			});
			await goto(`/deployments/${created.id}`);
		} catch (e) {
			if (e instanceof ApiError) {
				formError = ERROR_MESSAGES[e.message] ?? e.message;
			} else {
				formError = e instanceof Error ? e.message : 'Failed to create the deployment.';
			}
		} finally {
			submitting = false;
		}
	}
</script>

{#if loadError}
	<div class="mb-4 rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">
		{loadError}
	</div>
{/if}

<div class="grid max-w-3xl grid-cols-1 gap-6">
	<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<h2 class="text-sm font-semibold text-neutral-900">Deployment</h2>
		<div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
			<label class="block">
				<span class="text-sm font-medium text-neutral-700">Slug</span>
				<input
					type="text"
					bind:value={slug}
					placeholder="e.g. acme-support-bot"
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				/>
				<p class="mt-1 text-xs text-neutral-400">
					Must be unique across the whole platform - becomes part of the gateway URL
					(<code>/v1/deployments/&#123;slug&#125;/run</code>).
				</p>
			</label>
			<label class="block">
				<span class="text-sm font-medium text-neutral-700">Environment</span>
				<select
					bind:value={environment}
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				>
					<option value="production">production</option>
					<option value="staging">staging</option>
				</select>
				<p class="mt-1 text-xs text-neutral-400">
					A staging deployment can later be Promoted to a production one (PRD §6.3).
				</p>
			</label>
		</div>
	</section>

	<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<h2 class="text-sm font-semibold text-neutral-900">Target</h2>
		<div class="mt-3 flex gap-4 text-sm">
			<label class="flex items-center gap-2">
				<input type="radio" name="target-type" checked={targetType === 'agent'} onchange={() => selectTargetType('agent')} />
				Agent
			</label>
			<label class="flex items-center gap-2">
				<input
					type="radio"
					name="target-type"
					checked={targetType === 'workflow'}
					onchange={() => selectTargetType('workflow')}
				/>
				Workflow
			</label>
		</div>

		{#if targetType === 'agent'}
			<div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Agent</span>
					<select
						bind:value={selectedAgentId}
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					>
						<option value="" disabled>Select an agent</option>
						{#each agents as agent (agent.id)}
							<option value={agent.id}>{agent.display_name ?? agent.name}</option>
						{/each}
					</select>
				</label>
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Published version</span>
					<select
						bind:value={selectedAgentVersionId}
						disabled={!selectedAgentId}
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:bg-neutral-50"
					>
						<option value="" disabled>
							{!selectedAgentId
								? 'Select an agent first'
								: agentVersions.length === 0
									? 'No published version yet'
									: 'Select a version'}
						</option>
						{#each agentVersions as version (version.id)}
							<option value={version.id}>v{version.version_no}</option>
						{/each}
					</select>
				</label>
			</div>
		{:else}
			<div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Workflow</span>
					<select
						bind:value={selectedWorkflowId}
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
					>
						<option value="" disabled>Select a workflow</option>
						{#each workflows as workflow (workflow.id)}
							<option value={workflow.id}>{workflow.name}</option>
						{/each}
					</select>
				</label>
				<label class="block">
					<span class="text-sm font-medium text-neutral-700">Published version</span>
					<select
						bind:value={selectedWorkflowVersionId}
						disabled={!selectedWorkflowId}
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
							focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:bg-neutral-50"
					>
						<option value="" disabled>
							{!selectedWorkflowId
								? 'Select a workflow first'
								: workflowVersions.length === 0
									? 'No published version yet'
									: 'Select a version'}
						</option>
						{#each workflowVersions as version (version.id)}
							<option value={version.id}>v{version.version_no}</option>
						{/each}
					</select>
				</label>
			</div>
		{/if}
	</section>

	{#if formError}
		<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
			{formError}
		</div>
	{/if}

	<div>
		<button
			type="button"
			disabled={!canSubmit || submitting}
			onclick={submit}
			class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600
				disabled:cursor-not-allowed disabled:opacity-50"
		>
			{submitting ? 'Creating…' : 'Create deployment'}
		</button>
	</div>
</div>
