<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import {
		getKnowledgeBase,
		updateKnowledgeBase,
		deleteKnowledgeBase,
		listKnowledgeBaseDocuments,
		importKnowledgeBaseFiles,
		exportKnowledgeBase,
		searchKnowledgeBase
	} from '$lib/api/console';
	import type { KnowledgeBase, KbDocument, KbSearchResultItem } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { goto } from '$app/navigation';

	let kb = $state<KnowledgeBase | null>(null);
	let documents = $state<KbDocument[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let uploadFile = $state<File | null>(null);
	let uploading = $state(false);
	let uploadError = $state<string | null>(null);

	let searchQuery = $state('');
	let searchTopK = $state(5);
	let searching = $state(false);
	let searchError = $state<string | null>(null);
	let searchResults = $state<KbSearchResultItem[] | null>(null);

	let settingsForm = $state({ chunk_size: 800, chunk_overlap: 100, retrieval_mode: 'vector' as KnowledgeBase['retrieval_mode'], alpha: 0.6 });
	let savingSettings = $state(false);
	let settingsError = $state<string | null>(null);

	let deleting = $state(false);
	let exporting = $state(false);

	async function load(id: string) {
		loading = true;
		error = null;
		try {
			[kb, documents] = await Promise.all([getKnowledgeBase(id), listKnowledgeBaseDocuments(id)]);
			settingsForm = {
				chunk_size: kb.chunk_size,
				chunk_overlap: kb.chunk_overlap,
				retrieval_mode: kb.retrieval_mode,
				alpha: kb.alpha
			};
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load this knowledge base.';
		} finally {
			loading = false;
		}
	}

	onMount(() => load($page.params.id ?? ''));

	$effect(() => {
		headerContent.set({ menu: 'Knowledge Bases', title: kb ? kb.name : 'Knowledge base detail' });
	});

	async function refreshDocuments() {
		if (!kb) return;
		documents = await listKnowledgeBaseDocuments(kb.id);
	}

	async function upload() {
		if (!kb || !uploadFile) return;
		uploading = true;
		uploadError = null;
		try {
			const form = new FormData();
			form.append('file', uploadFile);
			await importKnowledgeBaseFiles(kb.id, form);
			uploadFile = null;
			await refreshDocuments();
		} catch (e) {
			uploadError = e instanceof Error ? e.message : 'Failed to import the file.';
		} finally {
			uploading = false;
		}
	}

	async function runSearch() {
		if (!kb || !searchQuery.trim()) return;
		searching = true;
		searchError = null;
		searchResults = null;
		try {
			const result = await searchKnowledgeBase(kb.id, { query: searchQuery, top_k: searchTopK });
			searchResults = result.results;
		} catch (e) {
			searchError = e instanceof Error ? e.message : 'Search failed.';
		} finally {
			searching = false;
		}
	}

	async function saveSettings() {
		if (!kb) return;
		savingSettings = true;
		settingsError = null;
		try {
			kb = await updateKnowledgeBase(kb.id, settingsForm);
		} catch (e) {
			settingsError = e instanceof Error ? e.message : 'Failed to save settings.';
		} finally {
			savingSettings = false;
		}
	}

	async function doExport() {
		if (!kb) return;
		exporting = true;
		try {
			const blob = await exportKnowledgeBase(kb.id);
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = `${kb.name}.zip`;
			link.click();
			URL.revokeObjectURL(url);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to export this knowledge base.';
		} finally {
			exporting = false;
		}
	}

	async function doDelete() {
		if (!kb) return;
		if (!confirm(`Delete "${kb.name}"? This removes its files, index and search history.`)) return;
		deleting = true;
		try {
			await deleteKnowledgeBase(kb.id);
			await goto('/knowledge-bases');
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to delete this knowledge base.';
			deleting = false;
		}
	}

	const statusColor: Record<KbDocument['status'], string> = {
		queued: 'text-neutral-500',
		processing: 'text-warning-dark',
		ready: 'text-primary',
		failed: 'text-danger'
	};
</script>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading knowledge base…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this knowledge base</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
	</div>
{:else if !kb}
	<p class="text-sm text-neutral-500">Knowledge base not found.</p>
{:else}
	<div class="space-y-6">
		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Import documents</h2>
			<p class="mt-1 text-xs text-neutral-500">
				A single OKF <code>.md</code> file, or a <code>.zip</code> of <code>.md</code> files — folders in
				the zip become each file's category. PDF/DOCX/TXT are not yet supported.
			</p>
			{#if uploadError}
				<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{uploadError}</div>
			{/if}
			<div class="mt-3 flex items-center gap-3">
				<input
					type="file"
					accept=".md,.zip"
					onchange={(e) => (uploadFile = (e.target as HTMLInputElement).files?.[0] ?? null)}
					class="text-sm"
				/>
				<button
					type="button"
					disabled={!uploadFile || uploading}
					onclick={upload}
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600 disabled:opacity-50"
				>
					{uploading ? 'Uploading…' : 'Upload'}
				</button>
			</div>
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<div class="flex items-center justify-between">
				<h2 class="text-sm font-semibold text-neutral-700">Documents</h2>
				<button type="button" class="text-xs text-primary underline" onclick={refreshDocuments}>Refresh</button>
			</div>
			{#if documents.length === 0}
				<p class="mt-3 text-sm text-neutral-500">No documents imported yet.</p>
			{:else}
				<table class="mt-3 min-w-full divide-y divide-neutral-200 text-sm">
					<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
						<tr>
							<th class="px-3 py-2">Path</th>
							<th class="px-3 py-2">Category</th>
							<th class="px-3 py-2">Status</th>
							<th class="px-3 py-2">Chunks</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-neutral-100">
						{#each documents as doc (doc.id)}
							<tr>
								<td class="px-3 py-2 font-mono text-xs">{doc.path}</td>
								<td class="px-3 py-2 text-neutral-600">{doc.category ?? '—'}</td>
								<td class="px-3 py-2 font-medium {statusColor[doc.status]}" title={doc.error ?? ''}>
									{doc.status}
								</td>
								<td class="px-3 py-2 text-neutral-600">{doc.chunk_count}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Search test</h2>
			{#if kb.retrieval_mode === 'hybrid'}
				<p class="mt-1 text-xs text-warning-dark">
					This KB is set to hybrid retrieval, which isn't implemented yet — search will fail until it's
					switched back to vector or hybrid retrieval ships.
				</p>
			{/if}
			<div class="mt-3 flex gap-2">
				<input
					type="text"
					bind:value={searchQuery}
					placeholder="Ask a question…"
					class="flex-1 rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				/>
				<input
					type="number"
					min="1"
					bind:value={searchTopK}
					class="w-20 rounded-md border border-neutral-300 px-3 py-2 text-sm"
				/>
				<button
					type="button"
					disabled={searching || !searchQuery.trim()}
					onclick={runSearch}
					class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600 disabled:opacity-50"
				>
					{searching ? 'Searching…' : 'Search'}
				</button>
			</div>
			{#if searchError}
				<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{searchError}</div>
			{/if}
			{#if searchResults}
				{#if searchResults.length === 0}
					<p class="mt-3 text-sm text-neutral-500">No results.</p>
				{:else}
					<ul class="mt-3 space-y-2">
						{#each searchResults as result, i (i)}
							<li class="rounded-md border border-neutral-200 p-3 text-sm">
								<div class="flex items-center justify-between text-xs text-neutral-400">
									<span>score: {result.score?.toFixed(4) ?? '—'}</span>
								</div>
								<p class="mt-1 whitespace-pre-wrap text-neutral-700">{result.content}</p>
							</li>
						{/each}
					</ul>
				{/if}
			{/if}
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Settings</h2>
			{#if settingsError}
				<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{settingsError}</div>
			{/if}
			<div class="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
				<label class="block">
					<span class="text-xs text-neutral-500">Chunk size</span>
					<input
						type="number"
						min="1"
						bind:value={settingsForm.chunk_size}
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm"
					/>
				</label>
				<label class="block">
					<span class="text-xs text-neutral-500">Chunk overlap</span>
					<input
						type="number"
						min="0"
						bind:value={settingsForm.chunk_overlap}
						class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm"
					/>
				</label>
				<label class="block">
					<span class="text-xs text-neutral-500">Retrieval mode</span>
					<select bind:value={settingsForm.retrieval_mode} class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm">
						<option value="vector">Vector</option>
						<option value="hybrid">Hybrid (search not yet supported)</option>
					</select>
				</label>
				<label class="block">
					<span class="text-xs text-neutral-500">Alpha</span>
					<input
						type="number"
						min="0"
						max="1"
						step="0.05"
						bind:value={settingsForm.alpha}
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
				<div class="flex gap-2">
					<button
						type="button"
						disabled={exporting}
						onclick={doExport}
						class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
					>
						{exporting ? 'Exporting…' : 'Export .zip'}
					</button>
					<button
						type="button"
						disabled={deleting}
						onclick={doDelete}
						class="rounded-md border border-danger/40 px-4 py-2 text-sm text-danger hover:bg-danger/10 disabled:opacity-50"
					>
						{deleting ? 'Deleting…' : 'Delete'}
					</button>
				</div>
			</div>
		</section>
	</div>
{/if}
