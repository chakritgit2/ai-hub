<script lang="ts">
	import { onMount } from 'svelte';
	import { listApiKeysForTenant, createApiKey, revokeApiKey, listDeploymentsForTenant } from '$lib/api/console';
	import type { ApiKey, ApiKeyCreated, Deployment } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId, tenantId } from '$lib/stores/company';
	import { currentUser, companyLabelForCompany } from '$lib/stores/auth';

	// Pilot of "each menu shows only its own tenant's data" (option A) - pools API keys
	// (for companies the caller is admin in, same gate as the single-company path's
	// requireAdminRole()) across every company under the selected tenant instead of the
	// usual single X-Company-Id company. See ApiKeysController::listApiKeys()'s
	// X-Tenant-Id branch and agents/+page.svelte, the first menu this pattern was applied
	// to. `deployments` is also fetched tenant-wide now (not just the current company) so
	// a pooled key's deploymentSlugs() lookup resolves correctly regardless of which
	// company it belongs to - the "+ New key" form's own checklist then filters it back
	// down to the current company, since a new key is still created into just that one.

	let apiKeys = $state<ApiKey[]>([]);
	let deployments = $state<Deployment[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showForm = $state(false);
	let copied = $state(false);
	let copyFailed = $state(false);
	let revokingId = $state<string | null>(null);
	let copiedPrefixId = $state<string | null>(null);

	let formDeployments = $derived(deployments.filter((d) => d.company_id === $companyId));

	function companyLabel(apiKeyCompanyId: string): string | undefined {
		return companyLabelForCompany($currentUser, apiKeyCompanyId);
	}

	let form = $state({
		name: '',
		rate_limit_per_min: 60,
		daily_cost_limit_usd: 5,
		allowed_ips: '',
		deployment_ids: [] as string[]
	});
	let formError = $state<string | null>(null);
	let submitting = $state(false);
	let justCreated = $state<ApiKeyCreated | null>(null);

	function deploymentLabel(deployment: Deployment): string {
		return `${deployment.slug} (${deployment.environment})`;
	}

	function toggleDeployment(deploymentId: string, checked: boolean) {
		form.deployment_ids = checked
			? [...form.deployment_ids, deploymentId]
			: form.deployment_ids.filter((id) => id !== deploymentId);
	}

	function deploymentSlugs(key: ApiKey): string {
		if (!key.deployment_ids || key.deployment_ids.length === 0) return 'none — unusable';
		const bySlug = key.deployment_ids.map(
			(id) => deployments.find((d) => d.id === id)?.slug ?? id
		);
		return bySlug.join(', ');
	}

	async function load() {
		loading = true;
		error = null;
		// No tenant resolved yet (e.g. a brand new SSO user with zero company
		// memberships at all, so CompanySwitcher never has anything to pick) - nothing
		// to fetch, but still clear the spinner rather than spinning forever. Same guard
		// as agents/+page.svelte's load().
		if (!$tenantId) {
			apiKeys = [];
			deployments = [];
			loading = false;
			return;
		}
		try {
			[apiKeys, deployments] = await Promise.all([
				listApiKeysForTenant($tenantId),
				listDeploymentsForTenant($tenantId)
			]);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load API keys.';
		} finally {
			loading = false;
		}
	}

	async function copyToClipboard(text: string): Promise<boolean> {
		let ok = false;
		if (navigator.clipboard) {
			// `navigator.clipboard` itself is undefined outside a secure context (HTTPS or
			// localhost) in every major browser - this console is currently served over
			// plain HTTP (no Ingress/TLS yet), so this branch never runs there today, only
			// the execCommand fallback below does. Still tried first since it's the real
			// API and this console will eventually sit behind TLS.
			try {
				await navigator.clipboard.writeText(text);
				ok = true;
			} catch {
				// Permission denied - fall through to the execCommand fallback below.
			}
		}
		if (!ok) {
			// `execCommand('copy')` is deprecated but, unlike the Clipboard API above,
			// doesn't require a secure context - the only way to offer a working copy
			// button on this console's current plain-HTTP deployment.
			const textarea = document.createElement('textarea');
			textarea.value = text;
			textarea.style.position = 'fixed';
			textarea.style.opacity = '0';
			document.body.appendChild(textarea);
			textarea.select();
			try {
				ok = document.execCommand('copy');
			} catch {
				ok = false;
			}
			document.body.removeChild(textarea);
		}
		return ok;
	}

	async function copyKey(key: string) {
		const ok = await copyToClipboard(key);
		copyFailed = !ok;
		copied = ok;
		setTimeout(() => {
			copied = false;
			copyFailed = false;
		}, 2000);
	}

	async function copyPrefix(key: ApiKey) {
		const ok = await copyToClipboard(key.prefix);
		copiedPrefixId = ok ? key.id : null;
		setTimeout(() => {
			if (copiedPrefixId === key.id) copiedPrefixId = null;
		}, 2000);
	}

	async function revoke(key: ApiKey) {
		// A key with a last_used_at is known to be in active use somewhere —
		// revoking it before the consuming app's config is updated to a new
		// key causes an immediate outage there (deployment_not_found at the
		// gateway), not just a clean 401. Warn harder in that case so the
		// overlap step doesn't get skipped during rotation.
		const message = key.last_used_at
			? `Revoke "${key.name}"? This key has been used as recently as ${new Date(key.last_used_at).toLocaleString()} — revoking it now will immediately break any app still using it. Make sure the consuming app's config has already been updated to a new key before continuing.`
			: `Revoke "${key.name}"? Any caller using it will get 404s immediately.`;
		if (!confirm(message)) return;
		revokingId = key.id;
		try {
			// revokeApiKey is X-Company-Id-scoped via the shared companyId store
			// (api/client.ts), but a pooled key can belong to any company under the
			// tenant - point companyId at that key's own company before acting, same
			// approach as approvals/+page.svelte's decide().
			companyId.set(key.company_id);
			await revokeApiKey(key.id);
			await load();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to revoke the key.';
		} finally {
			revokingId = null;
		}
	}

	onMount(load);
	$effect(() => {
		$tenantId;
		load();
	});

	$effect(() => {
		headerContent.set({ menu: 'API Keys', title: 'API Keys', actions: newKeyAction });
	});

	async function submitForm() {
		if (form.deployment_ids.length === 0) {
			formError = 'Select at least one deployment — a key scoped to none can never match anything on the gateway.';
			return;
		}
		submitting = true;
		formError = null;
		try {
			justCreated = await createApiKey({
				name: form.name,
				rate_limit_per_min: form.rate_limit_per_min,
				daily_cost_limit_usd: form.daily_cost_limit_usd,
				allowed_ips: form.allowed_ips
					.split(',')
					.map((v) => v.trim())
					.filter(Boolean),
				deployment_ids: form.deployment_ids
			});
			showForm = false;
			form = { name: '', rate_limit_per_min: 60, daily_cost_limit_usd: 5, allowed_ips: '', deployment_ids: [] };
			await load();
		} catch (e) {
			formError = e instanceof Error ? e.message : 'Failed to create the API key.';
		} finally {
			submitting = false;
		}
	}
</script>

{#snippet newKeyAction()}
	<button
		type="button"
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		onclick={() => (showForm = !showForm)}
	>
		+ New key
	</button>
{/snippet}

<p class="mb-4 text-sm text-neutral-600">
	API keys are long-lived credentials external apps use to call the gateway server-to-server
	(not for browsers — those use short-lived session tokens instead). Each key is scoped to one
	or more deployments and can carry its own IP allowlist, rate limit, and daily cost limit.
</p>

{#if justCreated}
	{@const createdKey = justCreated.key}
	<div class="mb-6 rounded-md border border-warning/40 bg-warning/10 p-4">
		<p class="text-sm font-medium text-warning-dark">Shown once — copy this key now</p>
		<code class="mt-2 block break-all rounded bg-white px-3 py-2 font-mono text-sm text-neutral-800">
			{createdKey}
		</code>
		<div class="mt-3 flex gap-2">
			<button
				type="button"
				class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
				onclick={() => copyKey(createdKey)}
			>
				{copied ? 'Copied!' : 'Copy'}
			</button>
			<button
				type="button"
				class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
				onclick={() => (justCreated = null)}
			>
				Done
			</button>
		</div>
		{#if copyFailed}
			<p class="mt-2 text-xs text-danger">
				Couldn't copy automatically — select the key above and copy it manually (Ctrl/Cmd+C).
			</p>
		{/if}
	</div>
{/if}

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
			<p class="mt-1 text-xs text-neutral-500">
				A label to recognize this key later — e.g. which integration or service uses it.
			</p>
		</label>
		<fieldset class="sm:col-span-2">
			<legend class="text-sm font-medium text-neutral-700">Scoped deployments *</legend>
			<p class="mt-1 text-xs text-neutral-500">
				Required — a key scoped to none can authenticate but will match nothing on the gateway
				(every call gets 404). Server-to-server only, browsers use 5-minute session tokens instead
				(PRD §6.3).
			</p>
			{#if formDeployments.length === 0}
				<p class="mt-2 text-sm text-neutral-500">No deployments yet for this company — publish one first.</p>
			{:else}
				<div class="mt-2 flex flex-col gap-1.5">
					{#each formDeployments as deployment (deployment.id)}
						<label
							class="flex items-center gap-2 text-sm"
							class:text-neutral-700={deployment.enabled}
							class:text-neutral-400={!deployment.enabled}
						>
							<input
								type="checkbox"
								value={deployment.id}
								disabled={!deployment.enabled}
								checked={form.deployment_ids.includes(deployment.id)}
								onchange={(e) =>
									toggleDeployment(deployment.id, (e.currentTarget as HTMLInputElement).checked)}
							/>
							{deploymentLabel(deployment)}
							{#if !deployment.enabled}
								<span class="text-xs">— disabled, can't be scoped</span>
							{/if}
						</label>
					{/each}
				</div>
			{/if}
		</fieldset>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Allowed IPs (comma-separated)</span>
			<input
				type="text"
				bind:value={form.allowed_ips}
				placeholder="203.0.113.4"
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
			<p class="mt-1 text-xs text-neutral-500">
				Restrict this key to calls from these IPs only. Leave blank to allow any IP.
			</p>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Rate limit / min</span>
			<input
				type="number"
				min="1"
				bind:value={form.rate_limit_per_min}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
			<p class="mt-1 text-xs text-neutral-500">
				Max requests per minute this key may make. Leave blank for no limit.
			</p>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Daily cost limit (USD)</span>
			<input
				type="number"
				min="0"
				step="0.01"
				bind:value={form.daily_cost_limit_usd}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
			<p class="mt-1 text-xs text-neutral-500">
				Max USD this key may spend per day across its scoped deployments. Leave blank for no
				limit.
			</p>
		</label>
		<div class="flex gap-2 sm:col-span-2">
			<button
				type="submit"
				disabled={submitting}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
			>
				{submitting ? 'Creating…' : 'Create key'}
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
	<p class="text-sm text-neutral-500" role="status">Loading API keys…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load API keys</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if apiKeys.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">No API keys yet for this tenant.</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Company</th>
					<th class="px-4 py-2" title="Short, non-secret identifier for this key. The full secret is only ever shown once, at creation.">
						Prefix
					</th>
					<th class="px-4 py-2" title="Deployments this key can call through the gateway.">Scope</th>
					<th class="px-4 py-2" title="Caller IPs allowed to use this key. 'any' means no IP restriction.">
						Allowed IPs
					</th>
					<th class="px-4 py-2" title="Max requests per minute this key may make.">Rate limit</th>
					<th class="px-4 py-2" title="Max USD this key may spend per day across its scoped deployments.">
						Daily cost limit
					</th>
					<th class="px-4 py-2" title="When this key was last used to call the gateway.">Last used</th>
					<th class="px-4 py-2" title="Revoked keys are rejected immediately and can't be reactivated.">
						Status
					</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each apiKeys as key (key.id)}
					<tr class={key.revoked_at ? 'opacity-50' : ''}>
						<td class="px-4 py-2 font-medium text-neutral-900">{key.name}</td>
						<td class="px-4 py-2 text-neutral-600">
							{#if companyLabel(key.company_id)}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
									{companyLabel(key.company_id)}
								</span>
							{/if}
						</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-600">
							<span class="inline-flex items-center gap-1.5">
								{key.prefix}••••
								<button
									type="button"
									title="Copy prefix"
									class="rounded border border-neutral-300 px-1.5 py-0.5 text-[11px] font-sans text-neutral-500 hover:bg-neutral-50"
									onclick={() => copyPrefix(key)}
								>
									{copiedPrefixId === key.id ? 'Copied!' : 'Copy'}
								</button>
							</span>
						</td>
						<td class="px-4 py-2 text-neutral-600">{deploymentSlugs(key)}</td>
						<td class="px-4 py-2 text-neutral-600">{(key.allowed_ips ?? []).join(', ') || 'any'}</td>
						<td class="px-4 py-2 text-neutral-600">
							{key.rate_limit_per_min ?? 'none'}
						</td>
						<td class="px-4 py-2 text-neutral-600">
							{key.daily_cost_limit_usd !== undefined && key.daily_cost_limit_usd !== null
								? `$${key.daily_cost_limit_usd}`
								: 'none'}
						</td>
						<td class="px-4 py-2 text-neutral-600">
							{key.last_used_at ? new Date(key.last_used_at).toLocaleString() : 'never'}
						</td>
						<td class="px-4 py-2">
							{#if key.revoked_at}
								<span class="text-xs font-medium text-neutral-500">revoked</span>
							{:else}
								<span class="text-xs font-medium text-primary-700">active</span>
							{/if}
						</td>
						<td class="px-4 py-2 text-right">
							{#if !key.revoked_at}
								<button
									type="button"
									disabled={revokingId === key.id}
									class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10"
									onclick={() => revoke(key)}
								>
									{revokingId === key.id ? 'Revoking…' : 'Revoke'}
								</button>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
