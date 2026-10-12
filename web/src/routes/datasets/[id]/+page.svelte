<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import {
		getDataset,
		updateDataset,
		deleteDataset,
		listDatasetItems,
		importDatasetItems
	} from '$lib/api/console';
	import type { Dataset, DatasetItem, DatasetImportResult } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { goto } from '$app/navigation';

	let dataset = $state<Dataset | null>(null);
	let items = $state<DatasetItem[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);

	let uploadFile = $state<File | null>(null);
	let uploading = $state(false);
	let uploadError = $state<string | null>(null);
	let uploadResult = $state<DatasetImportResult | null>(null);

	let nameForm = $state({ name: '' });
	let savingSettings = $state(false);
	let settingsError = $state<string | null>(null);

	let deleting = $state(false);

	async function load(id: string) {
		loading = true;
		error = null;
		try {
			[dataset, items] = await Promise.all([getDataset(id), listDatasetItems(id)]);
			nameForm = { name: dataset.name };
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load this dataset.';
		} finally {
			loading = false;
		}
	}

	onMount(() => load($page.params.id ?? ''));

	$effect(() => {
		headerContent.set({ menu: 'Datasets', title: dataset ? dataset.name : 'Dataset detail' });
	});

	async function refreshItems() {
		if (!dataset) return;
		items = await listDatasetItems(dataset.id);
	}

	async function upload() {
		if (!dataset || !uploadFile) return;
		uploading = true;
		uploadError = null;
		uploadResult = null;
		try {
			const form = new FormData();
			form.append('file', uploadFile);
			uploadResult = await importDatasetItems(dataset.id, form);
			uploadFile = null;
			await refreshItems();
		} catch (e) {
			uploadError = e instanceof Error ? e.message : 'Failed to import the file.';
		} finally {
			uploading = false;
		}
	}

	async function saveSettings() {
		if (!dataset) return;
		savingSettings = true;
		settingsError = null;
		try {
			dataset = await updateDataset(dataset.id, nameForm);
		} catch (e) {
			settingsError = e instanceof Error ? e.message : 'Failed to save settings.';
		} finally {
			savingSettings = false;
		}
	}

	async function doDelete() {
		if (!dataset) return;
		if (!confirm(`Delete "${dataset.name}"? This removes all of its items.`)) return;
		deleting = true;
		try {
			await deleteDataset(dataset.id);
			await goto('/datasets');
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to delete this dataset.';
			deleting = false;
		}
	}
</script>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading dataset…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this dataset</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
	</div>
{:else if !dataset}
	<p class="text-sm text-neutral-500">Dataset not found.</p>
{:else}
	<div class="space-y-6">
		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Import items</h2>
			<p class="mt-1 text-xs text-neutral-500">
				A <code>.csv</code> or <code>.json</code> file of rows with columns/keys
				<code>question</code> (required), <code>expected_answer</code>, <code>context</code> and
				<code>tags</code> (semicolon-separated in CSV, an array in JSON).
			</p>
			{#if uploadError}
				<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{uploadError}</div>
			{/if}
			<div class="mt-3 flex items-center gap-3">
				<input
					type="file"
					accept=".csv,.json"
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
			{#if uploadResult}
				<div class="mt-3 rounded-md border border-neutral-200 bg-neutral-50 p-3 text-sm">
					<p class="text-neutral-700">Inserted {uploadResult.inserted} item(s).</p>
					{#if uploadResult.errors.length > 0}
						<ul class="mt-2 list-disc space-y-1 pl-5 text-xs text-danger">
							{#each uploadResult.errors as err (err.row)}
								<li>Row {err.row}: {err.error}</li>
							{/each}
						</ul>
					{/if}
				</div>
			{/if}
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<div class="flex items-center justify-between">
				<h2 class="text-sm font-semibold text-neutral-700">Items</h2>
				<button type="button" class="text-xs text-primary underline" onclick={refreshItems}>Refresh</button>
			</div>
			{#if items.length === 0}
				<p class="mt-3 text-sm text-neutral-500">No items yet.</p>
			{:else}
				<table class="mt-3 min-w-full divide-y divide-neutral-200 text-sm">
					<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
						<tr>
							<th class="px-3 py-2">Question</th>
							<th class="px-3 py-2">Expected answer</th>
							<th class="px-3 py-2">Context</th>
							<th class="px-3 py-2">Tags</th>
						</tr>
					</thead>
					<tbody class="divide-y divide-neutral-100">
						{#each items as item (item.id)}
							<tr>
								<td class="px-3 py-2 text-neutral-900">{item.question}</td>
								<td class="px-3 py-2 text-neutral-600">{item.expected_answer ?? '—'}</td>
								<td class="px-3 py-2 text-neutral-600">{item.context ?? '—'}</td>
								<td class="px-3 py-2 text-neutral-600">{item.tags.join(', ') || '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Settings</h2>
			{#if settingsError}
				<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{settingsError}</div>
			{/if}
			<div class="mt-3">
				<label class="block">
					<span class="text-xs text-neutral-500">Name</span>
					<input
						type="text"
						bind:value={nameForm.name}
						class="mt-1 w-full max-w-sm rounded-md border border-neutral-300 px-3 py-2 text-sm"
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
					disabled={deleting}
					onclick={doDelete}
					class="rounded-md border border-danger/40 px-4 py-2 text-sm text-danger hover:bg-danger/10 disabled:opacity-50"
				>
					{deleting ? 'Deleting…' : 'Delete'}
				</button>
			</div>
		</section>
	</div>
{/if}
