<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { getDeployment } from '$lib/api/console';
	import type { Deployment } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';

	let deployment = $state<Deployment | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load(id: string) {
		loading = true;
		error = null;
		try {
			deployment = await getDeployment(id);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load this deployment.';
		} finally {
			loading = false;
		}
	}

	onMount(() => load($page.params.id ?? ''));

	$effect(() => {
		headerContent.set({
			menu: 'Deployments',
			title: deployment ? deployment.slug : 'Deployment detail',
			actions: headerActions
		});
	});

	let usagePercent = $state(42); // placeholder until Redis-backed usage is wired up
</script>

{#snippet headerActions()}
	<div class="flex gap-2">
		<button
			type="button"
			class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 shadow-sm hover:bg-neutral-50"
		>
			Rollback
		</button>
		<button
			type="button"
			class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		>
			Save changes
		</button>
	</div>
{/snippet}

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading deployment…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this deployment</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
	</div>
{:else if !deployment}
	<p class="text-sm text-neutral-500">Deployment not found.</p>
{:else}
	<div class="space-y-6">
		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Binding</h2>
			<dl class="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
				<div><dt class="text-xs text-neutral-400">Agent version</dt><dd class="font-mono text-sm">{deployment.agent_version_id}</dd></div>
				<div><dt class="text-xs text-neutral-400">Environment</dt><dd class="text-sm">{deployment.environment}</dd></div>
				<div><dt class="text-xs text-neutral-400">Config version</dt><dd class="text-sm">{deployment.config_version ?? '—'}</dd></div>
				<div><dt class="text-xs text-neutral-400">Enabled</dt><dd class="text-sm">{deployment.enabled ? 'Yes' : 'No'}</dd></div>
			</dl>
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Limits</h2>
			<dl class="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3">
				<div><dt class="text-xs text-neutral-400">Rate limit / min</dt><dd class="text-sm">{deployment.rate_limit_per_min ?? '—'}</dd></div>
				<div><dt class="text-xs text-neutral-400">Daily token limit</dt><dd class="text-sm">{deployment.daily_token_limit ?? '—'}</dd></div>
				<div><dt class="text-xs text-neutral-400">Daily cost limit</dt><dd class="text-sm">{deployment.daily_cost_limit_usd != null ? `$${deployment.daily_cost_limit_usd}` : '—'}</dd></div>
			</dl>
			<div class="mt-4">
				<div class="flex items-center justify-between text-xs text-neutral-500">
					<span>Used today (incl. reserved)</span>
					<span>{usagePercent}%</span>
				</div>
				<div class="mt-1 h-2 w-full overflow-hidden rounded-full bg-neutral-100">
					<div
						class="h-full rounded-full {usagePercent >= 80 ? 'bg-warning' : 'bg-primary'}"
						style="width: {usagePercent}%"
					></div>
				</div>
				{#if usagePercent >= 80}
					<p class="mt-1 text-xs text-warning-dark">
						Warning: at or above 80% of today's quota. Resets at 00:00 Asia/Bangkok. If Redis is
						down, quota fails closed for deployments with a daily cost limit (PRD §6.3).
					</p>
				{/if}
			</div>
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Runtime &amp; security</h2>
			<dl class="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3">
				<div><dt class="text-xs text-neutral-400">Allowed origins</dt><dd class="text-sm">{(deployment.allowed_origins ?? []).join(', ') || 'none'}</dd></div>
				<div><dt class="text-xs text-neutral-400">Output mode</dt><dd class="text-sm">{deployment.output_mode ?? 'stream'}</dd></div>
				<div><dt class="text-xs text-neutral-400">Conversation TTL</dt><dd class="text-sm">{deployment.conversation_ttl_days ?? 30} days</dd></div>
				<div><dt class="text-xs text-neutral-400">Allow write tools</dt><dd class="text-sm">{deployment.allow_write_tools ? 'Yes' : 'No'}</dd></div>
			</dl>
		</section>

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Call it</h2>
			<pre class="mt-3 overflow-x-auto rounded-md bg-neutral-900 p-3 font-mono text-xs text-neutral-100">curl -X POST https://[gateway-host]/v1/deployments/{deployment.slug}/run \
  -H "Authorization: Bearer ak_..." \
  -H "Content-Type: application/json" \
  -d '{'{'} "input": "hello" {'}'}'</pre>
		</section>
	</div>
{/if}
