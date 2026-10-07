<script lang="ts">
	import { onMount } from 'svelte';
	import { listConnections, createConnection, putConnectionSecret, testConnection } from '$lib/api/console';
	import type { Connection } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';

	let connections = $state<Connection[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showForm = $state(false);

	let form = $state({ name: '', type: 'dynamiq.connections.OpenAI', api_base: '', max_concurrency: 2 });
	let formError = $state<string | null>(null);
	let submitting = $state(false);

	let secretFormId = $state<string | null>(null);
	let secretValue = $state('');
	let secretError = $state<string | null>(null);
	let savingSecret = $state(false);
	let testResults = $state<Record<string, { ok: boolean; detail: string } | 'testing'>>({});

	async function load() {
		loading = true;
		error = null;
		try {
			connections = await listConnections();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load connections.';
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
		headerContent.set({ menu: 'Connections', title: 'Connections', actions: newConnectionAction });
	});

	async function submitForm() {
		submitting = true;
		formError = null;
		try {
			await createConnection(form);
			showForm = false;
			form = { name: '', type: 'dynamiq.connections.OpenAI', api_base: '', max_concurrency: 2 };
			await load();
		} catch (e) {
			formError = e instanceof Error ? e.message : 'Failed to create the connection.';
		} finally {
			submitting = false;
		}
	}

	function openSecretForm(connection: Connection) {
		secretFormId = connection.id;
		secretValue = '';
		secretError = null;
	}

	async function submitSecret(connectionId: string) {
		savingSecret = true;
		secretError = null;
		try {
			await putConnectionSecret(connectionId, { secret: secretValue });
			secretFormId = null;
			secretValue = '';
			await load();
		} catch (e) {
			secretError = e instanceof Error ? e.message : 'Failed to save the secret.';
		} finally {
			savingSecret = false;
		}
	}

	async function runTest(connectionId: string) {
		testResults = { ...testResults, [connectionId]: 'testing' };
		try {
			const result = await testConnection(connectionId);
			testResults = { ...testResults, [connectionId]: result };
		} catch (e) {
			testResults = {
				...testResults,
				[connectionId]: { ok: false, detail: e instanceof Error ? e.message : 'Test failed.' }
			};
		}
	}
</script>

{#snippet newConnectionAction()}
	<button
		type="button"
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		onclick={() => (showForm = !showForm)}
	>
		+ New connection
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
			<span class="text-sm font-medium text-neutral-700">Provider type</span>
			<input
				type="text"
				bind:value={form.type}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">API base</span>
			<input
				type="text"
				bind:value={form.api_base}
				placeholder="https://api.openai.com/v1"
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
			<span class="mt-1 block text-xs text-neutral-400">
				Checked against the egress allowlist on save (PRD §7.4).
			</span>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Max concurrency</span>
			<input
				type="number"
				min="1"
				bind:value={form.max_concurrency}
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
				{submitting ? 'Saving…' : 'Save connection'}
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
	<p class="text-sm text-neutral-500" role="status">Loading connections…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load connections</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if connections.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">
			No connections yet for this company — there are no shared connections across companies (PRD §7.7).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Type</th>
					<th class="px-4 py-2">API base</th>
					<th class="px-4 py-2">Key</th>
					<th class="px-4 py-2">Max concurrency</th>
					<th class="px-4 py-2">Actions</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each connections as connection (connection.id)}
					<tr>
						<td class="px-4 py-2 font-medium text-neutral-900">{connection.name}</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-600">{connection.type}</td>
						<td class="px-4 py-2 text-neutral-600">{connection.api_base ?? '—'}</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-500">{connection.masked_hint ?? '••••••••'}</td>
						<td class="px-4 py-2 text-neutral-600">{connection.max_concurrency ?? '—'}</td>
						<td class="px-4 py-2">
							<div class="flex flex-wrap items-center gap-2">
								<button
									type="button"
									class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
									onclick={() => openSecretForm(connection)}
								>
									Set key
								</button>
								<button
									type="button"
									class="rounded-md border border-neutral-300 px-2.5 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
									disabled={testResults[connection.id] === 'testing'}
									onclick={() => runTest(connection.id)}
								>
									{testResults[connection.id] === 'testing' ? 'Testing…' : 'Test'}
								</button>
								{#if testResults[connection.id] && testResults[connection.id] !== 'testing'}
									{@const result = testResults[connection.id] as { ok: boolean; detail: string }}
									<span class={`text-xs ${result.ok ? 'text-success' : 'text-danger'}`}>
										{result.ok ? '✓' : '✗'} {result.detail}
									</span>
								{/if}
							</div>
							{#if secretFormId === connection.id}
								<form
									class="mt-2 flex flex-col gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-2"
									onsubmit={(e) => {
										e.preventDefault();
										submitSecret(connection.id);
									}}
								>
									{#if secretError}
										<p class="text-xs text-danger">{secretError}</p>
									{/if}
									<input
										type="password"
										bind:value={secretValue}
										required
										placeholder="New API key / secret"
										autocomplete="off"
										class="w-full rounded-md border border-neutral-300 px-2 py-1 font-mono text-xs
											focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
									/>
									<div class="flex gap-2">
										<button
											type="submit"
											disabled={savingSecret}
											class="rounded-md bg-primary px-3 py-1 text-xs font-medium text-white hover:bg-primary-600"
										>
											{savingSecret ? 'Saving…' : 'Save'}
										</button>
										<button
											type="button"
											class="rounded-md border border-neutral-300 px-3 py-1 text-xs text-neutral-700 hover:bg-white"
											onclick={() => (secretFormId = null)}
										>
											Cancel
										</button>
									</div>
								</form>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
