<script lang="ts">
	import { onMount } from 'svelte';
	import { listKnowledgeBases, createKnowledgeBase, listConnections } from '$lib/api/console';
	import type { KnowledgeBase, Connection } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	let knowledgeBases = $state<KnowledgeBase[]>([]);
	let connections = $state<Connection[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showForm = $state(false);

	let form = $state({
		name: '',
		embedder_connection_id: '',
		chunk_size: 800,
		chunk_overlap: 100,
		retrieval_mode: 'vector' as KnowledgeBase['retrieval_mode'],
		alpha: 0.6
	});
	let formError = $state<string | null>(null);
	let submitting = $state(false);

	async function load() {
		loading = true;
		error = null;
		try {
			[knowledgeBases, connections] = await Promise.all([listKnowledgeBases(), listConnections()]);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load knowledge bases.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$companyId;
		load();
	});

	$effect(() => {
		headerContent.set({ menu: 'Knowledge Bases', title: 'Knowledge Bases', actions: newKbAction });
	});

	function resetForm() {
		form = {
			name: '',
			embedder_connection_id: '',
			chunk_size: 800,
			chunk_overlap: 100,
			retrieval_mode: 'vector',
			alpha: 0.6
		};
	}

	async function submitForm() {
		submitting = true;
		formError = null;
		try {
			await createKnowledgeBase({
				name: form.name,
				embedder_connection_id: form.embedder_connection_id,
				chunk_size: form.chunk_size,
				chunk_overlap: form.chunk_overlap,
				retrieval_mode: form.retrieval_mode,
				alpha: form.alpha
			});
			showForm = false;
			resetForm();
			await load();
		} catch (e) {
			formError = e instanceof Error ? e.message : 'Failed to create the knowledge base.';
		} finally {
			submitting = false;
		}
	}
</script>

{#snippet newKbAction()}
	<button
		type="button"
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		onclick={() => (showForm = !showForm)}
	>
		+ New knowledge base
	</button>
{/snippet}

{#if showForm}
	<form
		class="mb-6 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm sm:grid-cols-2"
		onsubmit={(e) => {
			e.preventDefault();
			submitForm();
		}}
	>
		{#if formError}
			<div class="sm:col-span-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
				{formError}
			</div>
		{/if}
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Name</span>
			<input
				type="text"
				bind:value={form.name}
				required
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Embedder connection</span>
			<select
				bind:value={form.embedder_connection_id}
				required
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			>
				<option value="" disabled>Select a connection</option>
				{#each connections as connection (connection.id)}
					<option value={connection.id}>{connection.name}</option>
				{/each}
			</select>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Chunk size</span>
			<input
				type="number"
				min="1"
				bind:value={form.chunk_size}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Chunk overlap</span>
			<input
				type="number"
				min="0"
				bind:value={form.chunk_overlap}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Retrieval mode</span>
			<select
				bind:value={form.retrieval_mode}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			>
				<option value="vector">Vector</option>
				<option value="hybrid">Hybrid (not yet supported for search — vector + keyword, later)</option>
			</select>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Alpha (vector/keyword weight)</span>
			<input
				type="number"
				min="0"
				max="1"
				step="0.05"
				bind:value={form.alpha}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<div class="flex gap-2 sm:col-span-2">
			<button
				type="submit"
				disabled={submitting}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
			>
				{submitting ? 'Saving…' : 'Save knowledge base'}
			</button>
			<button
				type="button"
				class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
				onclick={() => (showForm = false)}
			>
				Cancel
			</button>
		</div>
	</form>
{/if}

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading knowledge bases…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load knowledge bases</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if knowledgeBases.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">
			No knowledge bases yet for this company — import OKF Markdown files for an agent to search (PRD §6.6).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Retrieval mode</th>
					<th class="px-4 py-2">Chunk size</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each knowledgeBases as kb (kb.id)}
					<tr>
						<td class="px-4 py-2 font-medium text-neutral-900">
							<a href="/knowledge-bases/{kb.id}" class="text-primary underline">{kb.name}</a>
						</td>
						<td class="px-4 py-2 text-neutral-600">{kb.retrieval_mode}</td>
						<td class="px-4 py-2 text-neutral-600">{kb.chunk_size}</td>
						<td class="px-4 py-2 text-right">
							<a
								href="/knowledge-bases/{kb.id}"
								class="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
							>
								Manage
							</a>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
