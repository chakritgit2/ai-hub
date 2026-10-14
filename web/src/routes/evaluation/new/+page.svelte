<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		listAgents,
		listAgentVersions,
		listDatasets,
		estimateEvalCost,
		createEvalRun
	} from '$lib/api/console';
	import type { Agent, AgentVersion, Dataset, EvalCostEstimate } from '$lib/api/types';
	import { EVAL_METRICS } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	headerContent.set({ menu: 'Evaluation', title: 'New eval run' });

	let agents = $state<Agent[]>([]);
	let datasets = $state<Dataset[]>([]);
	let agentVersions = $state<AgentVersion[]>([]);
	let loadError = $state<string | null>(null);

	let selectedAgentId = $state('');
	let selectedAgentVersionId = $state('');
	let selectedDatasetId = $state('');
	let selectedMetrics = $state<string[]>([]);
	let judgeModel = $state('');

	let estimate = $state<EvalCostEstimate | null>(null);
	let estimating = $state(false);
	let estimateError = $state<string | null>(null);

	let submitting = $state(false);
	let formError = $state<string | null>(null);

	async function loadPickers() {
		loadError = null;
		try {
			[agents, datasets] = await Promise.all([listAgents(), listDatasets()]);
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Failed to load agents/datasets.';
		}
	}

	onMount(loadPickers);
	// Same "retry once the company switcher corrects a stale placeholder company_id"
	// pattern every list/detail page already uses ($companyId effect) - without this, a
	// first-load 404 (confirmed live: GET /admin/v1/agents and /admin/v1/datasets both
	// 404 briefly before company resolution finishes) would leave this page's pickers
	// permanently empty, since onMount alone never retries.
	$effect(() => {
		$companyId;
		loadPickers();
	});

	// Agent -> Agent Version cascade, same single-select-bound-to-an-API-list pattern
	// AgentSpecTabs.svelte already uses for the Model tab's Connection picker.
	$effect(() => {
		const agentId = selectedAgentId;
		selectedAgentVersionId = '';
		agentVersions = [];
		if (!agentId) {
			return;
		}
		listAgentVersions(agentId)
			.then((versions) => {
				agentVersions = versions;
			})
			.catch((e) => {
				loadError = e instanceof Error ? e.message : 'Failed to load agent versions.';
			});
	});

	function toggleMetric(name: string, checked: boolean) {
		selectedMetrics = checked ? [...selectedMetrics, name] : selectedMetrics.filter((m) => m !== name);
		// A new metric selection invalidates any estimate shown so far - it no longer
		// reflects what would actually be submitted.
		estimate = null;
	}

	let canEstimate = $derived(!!selectedAgentVersionId && !!selectedDatasetId && selectedMetrics.length > 0);

	async function runEstimate() {
		estimating = true;
		estimateError = null;
		estimate = null;
		try {
			estimate = await estimateEvalCost({
				agent_version_id: selectedAgentVersionId,
				dataset_id: selectedDatasetId,
				metrics: selectedMetrics,
				judge_model: judgeModel || undefined
			});
		} catch (e) {
			estimateError = e instanceof Error ? e.message : 'Failed to estimate cost.';
		} finally {
			estimating = false;
		}
	}

	async function submit() {
		submitting = true;
		formError = null;
		try {
			const run = await createEvalRun({
				agent_version_id: selectedAgentVersionId,
				dataset_id: selectedDatasetId,
				metrics: selectedMetrics,
				judge_model: judgeModel || null,
				est_cost_usd: estimate?.estimated_cost_usd.total ?? null
			});
			await goto(`/evaluation/${run.id}`);
		} catch (e) {
			formError = e instanceof Error ? e.message : 'Failed to create the eval run.';
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
		<h2 class="text-sm font-semibold text-neutral-900">Agent version</h2>
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
				<span class="text-sm font-medium text-neutral-700">Version</span>
				<select
					bind:value={selectedAgentVersionId}
					disabled={!selectedAgentId || agentVersions.length === 0}
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:bg-neutral-50"
				>
					<option value="" disabled>
						{selectedAgentId ? 'Select a version' : 'Select an agent first'}
					</option>
					{#each agentVersions as version (version.id)}
						<option value={version.id}>v{version.version_no}{version.is_published ? ' (published)' : ''}</option>
					{/each}
				</select>
			</label>
		</div>
	</section>

	<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<h2 class="text-sm font-semibold text-neutral-900">Dataset</h2>
		<label class="mt-3 block">
			<span class="text-sm font-medium text-neutral-700">Dataset</span>
			{#if datasets.length === 0}
				<p class="mt-1 text-sm text-neutral-500">
					No datasets yet. <a href="/datasets" class="text-primary underline">Create one</a> first.
				</p>
			{:else}
				<select
					bind:value={selectedDatasetId}
					class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				>
					<option value="" disabled>Select a dataset</option>
					{#each datasets as dataset (dataset.id)}
						<option value={dataset.id}>{dataset.name}</option>
					{/each}
				</select>
			{/if}
		</label>
	</section>

	<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<h2 class="text-sm font-semibold text-neutral-900">Metrics</h2>
		<ul class="mt-3 divide-y divide-neutral-200 rounded-md border border-neutral-200">
			{#each EVAL_METRICS as metric (metric.name)}
				<li class="flex items-start gap-3 p-3">
					<input
						type="checkbox"
						id={`metric-${metric.name}`}
						class="mt-1"
						disabled={!metric.executable}
						checked={selectedMetrics.includes(metric.name)}
						onchange={(e) => toggleMetric(metric.name, (e.currentTarget as HTMLInputElement).checked)}
					/>
					<label for={`metric-${metric.name}`} class="flex-1 {metric.executable ? '' : 'opacity-60'}">
						<p class="text-sm font-medium text-neutral-900">{metric.label}</p>
						{#if !metric.executable}
							<p class="text-xs text-neutral-500">Not executable yet.</p>
						{/if}
					</label>
				</li>
			{/each}
		</ul>

		<label class="mt-4 block">
			<span class="text-sm font-medium text-neutral-700">Judge model</span>
			<input
				type="text"
				bind:value={judgeModel}
				placeholder="Required if any selected metric needs an LLM judge (e.g. gpt-4o-mini)"
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
	</section>

	<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<div class="flex items-center justify-between">
			<h2 class="text-sm font-semibold text-neutral-900">Cost estimate</h2>
			<button
				type="button"
				disabled={!canEstimate || estimating}
				onclick={runEstimate}
				class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50
					disabled:cursor-not-allowed disabled:opacity-50"
			>
				{estimating ? 'Estimating…' : 'Estimate cost'}
			</button>
		</div>

		{#if estimateError}
			<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
				{estimateError}
			</div>
		{:else if estimate}
			<dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
				<dt class="text-neutral-500">Items</dt>
				<dd class="text-neutral-900">{estimate.item_count}</dd>
				<dt class="text-neutral-500">Agent model</dt>
				<dd class="text-neutral-900">{estimate.agent_model}</dd>
				<dt class="text-neutral-500">Agent cost</dt>
				<dd class="text-neutral-900">
					{estimate.estimated_cost_usd.agent !== null ? `$${estimate.estimated_cost_usd.agent.toFixed(4)}` : '—'}
				</dd>
				<dt class="text-neutral-500">Judge cost</dt>
				<dd class="text-neutral-900">
					{estimate.estimated_cost_usd.judge !== null ? `$${estimate.estimated_cost_usd.judge.toFixed(4)}` : '—'}
				</dd>
				<dt class="text-neutral-500">Total</dt>
				<dd class="font-medium text-neutral-900">
					{estimate.estimated_cost_usd.total !== null ? `$${estimate.estimated_cost_usd.total.toFixed(4)}` : '—'}
				</dd>
			</dl>
			{#if estimate.warnings.length > 0}
				<ul class="mt-3 list-inside list-disc text-xs text-warning-dark">
					{#each estimate.warnings as warning}
						<li>{warning}</li>
					{/each}
				</ul>
			{/if}
		{:else}
			<p class="mt-3 text-sm text-neutral-500">Pick an agent version, a dataset, and at least one metric, then estimate.</p>
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
			disabled={!canEstimate || submitting}
			onclick={submit}
			class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600
				disabled:cursor-not-allowed disabled:opacity-50"
		>
			{submitting ? 'Starting…' : 'Run evaluation'}
		</button>
	</div>
</div>
