<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import {
		getDeployment,
		getDeploymentUsage,
		updateDeployment,
		deleteDeployment,
		promoteDeployment,
		listAgents,
		listAgentVersions,
		listWorkflows,
		listWorkflowVersions,
		listDeployments
	} from '$lib/api/console';
	import { ApiError } from '$lib/api/client';
	import type { AgentVersion, Deployment, DeploymentUsage, WorkflowVersion } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import UsageMeter from '$lib/components/UsageMeter.svelte';

	let deployment = $state<Deployment | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	// Loaded separately from `deployment` (own loading/error state) so a Redis hiccup on
	// the usage read never blocks the rest of the page - everything above this section
	// comes straight from Postgres via getDeployment() and shouldn't wait on Redis at all.
	let usage = $state<DeploymentUsage | null>(null);
	let usageLoading = $state(true);
	let usageError = $state<string | null>(null);

	async function load(id: string) {
		loading = true;
		error = null;
		try {
			deployment = await getDeployment(id);
			seedSettingsForm(deployment);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load this deployment.';
		} finally {
			loading = false;
		}
	}

	const USAGE_ERROR_MESSAGES: Record<string, string> = {
		usage_request_failed: 'Could not reach the gateway for usage data - try again shortly.',
		not_found: 'This deployment no longer exists - it may have been deleted.'
	};

	async function loadUsage(id: string) {
		usageLoading = true;
		usageError = null;
		try {
			usage = await getDeploymentUsage(id);
		} catch (e) {
			usageError =
				e instanceof ApiError
					? (USAGE_ERROR_MESSAGES[e.message] ?? e.message)
					: e instanceof Error
						? e.message
						: 'Failed to load usage.';
		} finally {
			usageLoading = false;
		}
	}

	onMount(() => {
		const id = $page.params.id ?? '';
		load(id);
		loadUsage(id);
	});

	$effect(() => {
		headerContent.set({
			menu: 'Deployments',
			title: deployment ? deployment.slug : 'Deployment detail',
			actions: headerActions
		});
	});

	// ---- Settings (Save changes) ---------------------------------------------------
	// Everything a deployment exposes besides its agent/workflow version binding
	// (that's Rollback, below) - matches console-api's own PATCH /deployments/{id}
	// field set exactly (DeploymentsController::parseOptionalFields).

	interface SettingsForm {
		rate_limit_per_min: string;
		daily_token_limit: string;
		daily_cost_limit_usd: string;
		allowed_origins: string;
		output_mode: 'stream' | 'buffered';
		allow_write_tools: boolean;
		conversation_ttl_days: string;
		enabled: boolean;
		guardrail_overrides: string;
	}

	let settingsOpen = $state(false);
	let settingsForm = $state<SettingsForm>({
		rate_limit_per_min: '',
		daily_token_limit: '',
		daily_cost_limit_usd: '',
		allowed_origins: '',
		output_mode: 'stream',
		allow_write_tools: false,
		conversation_ttl_days: '',
		enabled: true,
		guardrail_overrides: '{}'
	});
	let savingSettings = $state(false);
	let settingsError = $state<string | null>(null);

	function seedSettingsForm(d: Deployment) {
		settingsForm = {
			rate_limit_per_min: d.rate_limit_per_min != null ? String(d.rate_limit_per_min) : '',
			daily_token_limit: d.daily_token_limit != null ? String(d.daily_token_limit) : '',
			daily_cost_limit_usd: d.daily_cost_limit_usd != null ? String(d.daily_cost_limit_usd) : '',
			allowed_origins: (d.allowed_origins ?? []).join('\n'),
			output_mode: d.output_mode ?? 'stream',
			allow_write_tools: d.allow_write_tools ?? false,
			conversation_ttl_days: d.conversation_ttl_days != null ? String(d.conversation_ttl_days) : '',
			enabled: d.enabled,
			guardrail_overrides: JSON.stringify(d.guardrail_overrides ?? {}, null, 2)
		};
	}

	function toggleSettings() {
		settingsError = null;
		if (!settingsOpen && deployment) seedSettingsForm(deployment);
		settingsOpen = !settingsOpen;
	}

	async function saveSettings() {
		if (!deployment) return;
		settingsError = null;

		let guardrailOverrides: Record<string, unknown>;
		try {
			guardrailOverrides = JSON.parse(settingsForm.guardrail_overrides || '{}');
		} catch {
			settingsError = 'Guardrail overrides must be valid JSON.';
			return;
		}

		savingSettings = true;
		try {
			deployment = await updateDeployment(deployment.id, {
				rate_limit_per_min: settingsForm.rate_limit_per_min ? Number(settingsForm.rate_limit_per_min) : undefined,
				daily_token_limit: settingsForm.daily_token_limit ? Number(settingsForm.daily_token_limit) : null,
				daily_cost_limit_usd: settingsForm.daily_cost_limit_usd
					? Number(settingsForm.daily_cost_limit_usd)
					: null,
				allowed_origins: settingsForm.allowed_origins
					.split('\n')
					.map((o) => o.trim())
					.filter(Boolean),
				output_mode: settingsForm.output_mode,
				allow_write_tools: settingsForm.allow_write_tools,
				conversation_ttl_days: settingsForm.conversation_ttl_days
					? Number(settingsForm.conversation_ttl_days)
					: undefined,
				enabled: settingsForm.enabled,
				guardrail_overrides: guardrailOverrides
			});
			settingsOpen = false;
		} catch (e) {
			settingsError = e instanceof Error ? e.message : 'Failed to save changes.';
		} finally {
			savingSettings = false;
		}
	}

	// ---- Rollback ---------------------------------------------------------------
	// Only a *published* version of the *same* agent/workflow this deployment already
	// targets is a valid rollback target (console-api rejects anything else with
	// agent_mismatch/workflow_mismatch, PRD/commit 7f29bea) - there's no "get this
	// version's parent agent/workflow id" endpoint, so this resolves it client-side by
	// scanning this company's agents/workflows for the one whose versions include the
	// deployment's current version id. Done lazily (only when Rollback is opened), not
	// on page load, since most visits never need it.

	let rollbackOpen = $state(false);
	let rollbackLoading = $state(false);
	let rollbackError = $state<string | null>(null);
	let rollbackCandidates = $state<(AgentVersion | WorkflowVersion)[]>([]);
	let selectedRollbackVersionId = $state('');
	let rollingBack = $state(false);

	// allSettled, not all - one agent/workflow whose /versions call happens to fail (e.g.
	// deleted/archived concurrently by another admin, a transient blip) must not break
	// Rollback for every *other* deployment too; it just can't be the match, same as if
	// it legitimately doesn't own this version.
	async function findOwningAgentId(versionId: string): Promise<string | null> {
		const agents = await listAgents();
		const results = await Promise.allSettled(
			agents.map(async (agent) => ({ agent, versions: await listAgentVersions(agent.id) }))
		);
		for (const result of results) {
			if (result.status === 'fulfilled' && result.value.versions.some((v) => v.id === versionId)) {
				return result.value.agent.id;
			}
		}
		return null;
	}

	async function findOwningWorkflowId(versionId: string): Promise<string | null> {
		const workflows = await listWorkflows();
		const results = await Promise.allSettled(
			workflows.map(async (workflow) => ({ workflow, versions: await listWorkflowVersions(workflow.id) }))
		);
		for (const result of results) {
			if (result.status === 'fulfilled' && result.value.versions.some((v) => v.id === versionId)) {
				return result.value.workflow.id;
			}
		}
		return null;
	}

	async function openRollback() {
		if (!deployment) return;
		rollbackOpen = true;
		rollbackError = null;
		rollbackCandidates = [];
		selectedRollbackVersionId = '';
		rollbackLoading = true;
		try {
			if (deployment.agent_version_id) {
				const agentId = await findOwningAgentId(deployment.agent_version_id);
				if (!agentId) throw new Error('Could not find the agent this deployment belongs to.');
				const versions = await listAgentVersions(agentId);
				rollbackCandidates = versions.filter((v) => v.is_published);
			} else if (deployment.workflow_version_id) {
				const workflowId = await findOwningWorkflowId(deployment.workflow_version_id);
				if (!workflowId) throw new Error('Could not find the workflow this deployment belongs to.');
				const versions = await listWorkflowVersions(workflowId);
				rollbackCandidates = versions.filter((v) => v.is_published);
			}
		} catch (e) {
			rollbackError = e instanceof Error ? e.message : 'Failed to load versions to roll back to.';
		} finally {
			rollbackLoading = false;
		}
	}

	async function confirmRollback() {
		if (!deployment || !selectedRollbackVersionId) return;
		const target = rollbackCandidates.find((v) => v.id === selectedRollbackVersionId);
		if (!target) return;
		if (!confirm(`Roll back ${deployment.slug} to v${target.version_no}? This takes effect immediately.`)) {
			return;
		}
		rollingBack = true;
		rollbackError = null;
		try {
			deployment = await updateDeployment(
				deployment.id,
				deployment.agent_version_id
					? { agent_version_id: selectedRollbackVersionId }
					: { workflow_version_id: selectedRollbackVersionId }
			);
			rollbackOpen = false;
		} catch (e) {
			rollbackError = e instanceof Error ? e.message : 'Failed to roll back.';
		} finally {
			rollingBack = false;
		}
	}

	// ---- Delete -------------------------------------------------------------------

	let deleting = $state(false);
	let deleteError = $state<string | null>(null);

	async function doDelete() {
		if (!deployment) return;
		if (!confirm(`Delete deployment "${deployment.slug}"? This cannot be undone.`)) return;
		deleting = true;
		deleteError = null;
		try {
			await deleteDeployment(deployment.id);
			await goto('/deployments');
		} catch (e) {
			// console-api's FK-violation handler only ever sends {"error": "deployment_in_use"}
			// (DeploymentsController::deleteDeployment) - no list of which API keys, so there's
			// nothing more specific to surface than this.
			if (e instanceof ApiError && e.message === 'deployment_in_use') {
				deleteError = "Can't delete - still in use by one or more API keys. Revoke those first.";
			} else {
				deleteError = e instanceof Error ? e.message : 'Failed to delete this deployment.';
			}
			deleting = false;
		}
	}

	// ---- Promote --------------------------------------------------------------------
	// Promoting only ever copies the agent_version_id/workflow_version_id onto the
	// target (PRD/console-api comment) - quota/CORS/guardrails/output_mode are NOT
	// copied, so the confirm() below says so explicitly.

	let promoteOpen = $state(false);
	let promoteLoading = $state(false);
	let promoteError = $state<string | null>(null);
	let promoteCandidates = $state<Deployment[]>([]);
	let selectedPromoteTargetId = $state('');
	let promoting = $state(false);

	async function openPromote() {
		if (!deployment) return;
		promoteOpen = true;
		promoteError = null;
		promoteCandidates = [];
		selectedPromoteTargetId = deployment.promoted_to_id ?? '';
		promoteLoading = true;
		try {
			const all = await listDeployments();
			const isAgentTarget = deployment.agent_version_id != null;
			promoteCandidates = all.filter(
				(d) =>
					d.id !== deployment!.id &&
					d.environment === 'production' &&
					(isAgentTarget ? d.agent_version_id != null : d.workflow_version_id != null)
			);
		} catch (e) {
			promoteError = e instanceof Error ? e.message : 'Failed to load production deployments.';
		} finally {
			promoteLoading = false;
		}
	}

	async function confirmPromote() {
		if (!deployment || !selectedPromoteTargetId) return;
		const target = promoteCandidates.find((d) => d.id === selectedPromoteTargetId);
		if (!target) return;
		if (
			!confirm(
				`Promote to "${target.slug}"? This only copies the agent/workflow version - ` +
					'limits, CORS, guardrail overrides, and output mode are NOT copied and stay as configured on the target.'
			)
		) {
			return;
		}
		promoting = true;
		promoteError = null;
		try {
			// promoteDeployment's response is the TARGET (production) row, not this
			// (staging) one, DeploymentsController::promoteDeployment's final SELECT is
			// `WHERE id = :targetId` - assigning it here would silently swap this page
			// into showing/operating on a different deployment than the URL says. Reload
			// this deployment by its own id instead, to pick up its updated
			// `promoted_to_id`.
			await promoteDeployment(deployment.id, selectedPromoteTargetId);
			deployment = await getDeployment(deployment.id);
			promoteOpen = false;
		} catch (e) {
			promoteError = e instanceof Error ? e.message : 'Failed to promote.';
		} finally {
			promoting = false;
		}
	}
</script>

{#snippet headerActions()}
	{#if deployment}
		<div class="flex gap-2">
			{#if deployment.environment === 'staging'}
				<button
					type="button"
					disabled={promoting}
					onclick={openPromote}
					class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
				>
					Promote
				</button>
			{/if}
			<button
				type="button"
				disabled={deleting}
				onclick={doDelete}
				class="rounded-md border border-danger/40 bg-white px-4 py-2 text-sm font-medium text-danger shadow-sm hover:bg-danger/10 disabled:opacity-50"
			>
				{deleting ? 'Deleting…' : 'Delete'}
			</button>
			<button
				type="button"
				disabled={rollingBack}
				onclick={openRollback}
				class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 shadow-sm hover:bg-neutral-50 disabled:opacity-50"
			>
				Rollback
			</button>
			<button
				type="button"
				onclick={toggleSettings}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
			>
				{settingsOpen ? 'Close' : 'Save changes'}
			</button>
		</div>
	{/if}
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
		{#if deleteError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{deleteError}</div>
		{/if}

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Binding</h2>
			<dl class="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
				<div>
					<dt class="text-xs text-neutral-400">{deployment.agent_version_id ? 'Agent version' : 'Workflow version'}</dt>
					<dd class="font-mono text-sm">{deployment.agent_version_id ?? deployment.workflow_version_id}</dd>
				</div>
				<div><dt class="text-xs text-neutral-400">Environment</dt><dd class="text-sm">{deployment.environment}</dd></div>
				<div><dt class="text-xs text-neutral-400">Config version</dt><dd class="text-sm">{deployment.config_version ?? '—'}</dd></div>
				<div><dt class="text-xs text-neutral-400">Enabled</dt><dd class="text-sm">{deployment.enabled ? 'Yes' : 'No'}</dd></div>
			</dl>

			{#if rollbackOpen}
				<div class="mt-4 rounded-md border border-neutral-200 bg-neutral-50 p-3">
					<p class="text-sm font-medium text-neutral-700">Roll back to a published version</p>
					{#if rollbackLoading}
						<p class="mt-2 text-sm text-neutral-500">Loading versions…</p>
					{:else if rollbackError}
						<p class="mt-2 text-sm text-danger">{rollbackError}</p>
					{:else if rollbackCandidates.length === 0}
						<p class="mt-2 text-sm text-neutral-500">No other published version to roll back to.</p>
					{:else}
						<div class="mt-2 flex items-center gap-2">
							<select
								bind:value={selectedRollbackVersionId}
								class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
							>
								<option value="" disabled>Select a version</option>
								{#each rollbackCandidates as version (version.id)}
									<option value={version.id}>v{version.version_no}</option>
								{/each}
							</select>
							<button
								type="button"
								disabled={!selectedRollbackVersionId || rollingBack}
								onclick={confirmRollback}
								class="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50"
							>
								{rollingBack ? 'Rolling back…' : 'Confirm rollback'}
							</button>
							<button
								type="button"
								onclick={() => (rollbackOpen = false)}
								class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
							>
								Cancel
							</button>
						</div>
					{/if}
				</div>
			{/if}

			{#if promoteOpen}
				<div class="mt-4 rounded-md border border-neutral-200 bg-neutral-50 p-3">
					<p class="text-sm font-medium text-neutral-700">Promote to a production deployment</p>
					<p class="mt-1 text-xs text-neutral-500">
						Only the agent/workflow version is copied - limits, CORS, guardrail overrides, and output
						mode stay as configured on the target.
					</p>
					{#if promoteLoading}
						<p class="mt-2 text-sm text-neutral-500">Loading production deployments…</p>
					{:else if promoteError}
						<p class="mt-2 text-sm text-danger">{promoteError}</p>
					{:else if promoteCandidates.length === 0}
						<p class="mt-2 text-sm text-neutral-500">No matching production deployment exists yet.</p>
					{:else}
						<div class="mt-2 flex items-center gap-2">
							<select
								bind:value={selectedPromoteTargetId}
								class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
							>
								<option value="" disabled>Select a target deployment</option>
								{#each promoteCandidates as candidate (candidate.id)}
									<option value={candidate.id}>{candidate.slug}</option>
								{/each}
							</select>
							<button
								type="button"
								disabled={!selectedPromoteTargetId || promoting}
								onclick={confirmPromote}
								class="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50"
							>
								{promoting ? 'Promoting…' : 'Confirm promote'}
							</button>
							<button
								type="button"
								onclick={() => (promoteOpen = false)}
								class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
							>
								Cancel
							</button>
						</div>
					{/if}
				</div>
			{/if}
		</section>

		{#if settingsOpen}
			<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
				<h2 class="text-sm font-semibold text-neutral-700">Edit settings</h2>
				{#if settingsError}
					<div class="mt-3 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{settingsError}</div>
				{/if}
				<div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Rate limit / min</span>
						<input
							type="number"
							min="1"
							bind:value={settingsForm.rate_limit_per_min}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Daily token limit</span>
						<input
							type="number"
							min="0"
							placeholder="none"
							bind:value={settingsForm.daily_token_limit}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Daily cost limit (USD)</span>
						<input
							type="number"
							min="0"
							step="0.01"
							placeholder="none"
							bind:value={settingsForm.daily_cost_limit_usd}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm"
						/>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Output mode</span>
						<select bind:value={settingsForm.output_mode} class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm">
							<option value="stream">stream</option>
							<option value="buffered">buffered</option>
						</select>
					</label>
					<label class="block">
						<span class="text-sm font-medium text-neutral-700">Conversation TTL (days)</span>
						<input
							type="number"
							min="1"
							bind:value={settingsForm.conversation_ttl_days}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm"
						/>
					</label>
					<div class="flex items-end gap-4 pb-2">
						<label class="flex items-center gap-2 text-sm text-neutral-700">
							<input type="checkbox" bind:checked={settingsForm.allow_write_tools} />
							Allow write tools
						</label>
						<label class="flex items-center gap-2 text-sm text-neutral-700">
							<input type="checkbox" bind:checked={settingsForm.enabled} />
							Enabled
						</label>
					</div>
					<label class="block sm:col-span-3">
						<span class="text-sm font-medium text-neutral-700">Allowed origins (CORS)</span>
						<textarea
							rows="3"
							placeholder="One origin per line, e.g. https://example.com - never &quot;*&quot; (PRD §6.3)"
							bind:value={settingsForm.allowed_origins}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm"
						></textarea>
					</label>
					<label class="block sm:col-span-3">
						<span class="text-sm font-medium text-neutral-700">Guardrail overrides (JSON)</span>
						<textarea
							rows="4"
							bind:value={settingsForm.guardrail_overrides}
							class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm"
						></textarea>
						<p class="mt-1 text-xs text-neutral-400">
							Appended on top of the agent version's own guardrails - can only tighten, never loosen (PRD §6.4).
						</p>
					</label>
				</div>
				<div class="mt-4 flex gap-2">
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
						onclick={() => (settingsOpen = false)}
						class="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50"
					>
						Cancel
					</button>
				</div>
			</section>
		{/if}

		<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Limits</h2>
			<dl class="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3">
				<div><dt class="text-xs text-neutral-400">Rate limit / min</dt><dd class="text-sm">{deployment.rate_limit_per_min ?? '—'}</dd></div>
				<div><dt class="text-xs text-neutral-400">Daily token limit</dt><dd class="text-sm">{deployment.daily_token_limit ?? '—'}</dd></div>
				<div><dt class="text-xs text-neutral-400">Daily cost limit</dt><dd class="text-sm">{deployment.daily_cost_limit_usd != null ? `$${deployment.daily_cost_limit_usd}` : '—'}</dd></div>
			</dl>
			<div class="mt-4 border-t border-neutral-100 pt-4">
				<p class="text-sm font-medium text-neutral-700">Usage</p>
				{#if usageLoading}
					<p class="mt-2 text-xs text-neutral-400" role="status">Loading usage…</p>
				{:else if usageError}
					<p class="mt-2 text-xs text-danger">{usageError}</p>
				{:else if usage?.redis_unavailable}
					<p class="mt-2 text-xs text-neutral-400">
						Usage counters are temporarily unavailable (Redis unreachable) - limits are still
						enforced live by the gateway, this is only the display.
					</p>
				{:else if usage}
					<div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
						<UsageMeter label="This minute" value={usage.requests_this_minute} limit={usage.rate_limit_per_min} unit="req" />
						<UsageMeter label="Today" value={usage.tokens_today} limit={usage.daily_token_limit} unit="tok" />
						<UsageMeter
							label="Today"
							value={usage.cost_today_usd}
							limit={usage.daily_cost_limit_usd}
							unit="$"
							format={(n) => `$${n.toFixed(4)}`}
						/>
					</div>
					<p class="mt-2 text-xs text-neutral-400">
						Resets {new Date(usage.reset_at).toLocaleString()}
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
			<div class="mt-3 space-y-4">
				<div>
					<p class="text-xs font-medium text-neutral-500">cURL</p>
					<pre class="mt-1 overflow-x-auto rounded-md bg-neutral-900 p-3 font-mono text-xs text-neutral-100">curl -X POST https://[gateway-host]/v1/deployments/{deployment.slug}/run \
  -H "Authorization: Bearer ak_..." \
  -H "Content-Type: application/json" \
  -d '{'{'} "input": "hello" {'}'}'</pre>
				</div>
				<div>
					<p class="text-xs font-medium text-neutral-500">PHP</p>
					<pre class="mt-1 overflow-x-auto rounded-md bg-neutral-900 p-3 font-mono text-xs text-neutral-100">$ch = curl_init('https://[gateway-host]/v1/deployments/{deployment.slug}/run');
curl_setopt_array($ch, [
  CURLOPT_POST =&gt; true,
  CURLOPT_RETURNTRANSFER =&gt; true,
  CURLOPT_HTTPHEADER =&gt; [
    'Authorization: Bearer ak_...',
    'Content-Type: application/json',
  ],
  CURLOPT_POSTFIELDS =&gt; json_encode(['input' =&gt; 'hello']),
]);
$response = curl_exec($ch);</pre>
				</div>
				<div>
					<p class="text-xs font-medium text-neutral-500">Node</p>
					<pre class="mt-1 overflow-x-auto rounded-md bg-neutral-900 p-3 font-mono text-xs text-neutral-100">const res = await fetch('https://[gateway-host]/v1/deployments/{deployment.slug}/run', {'{'}
  method: 'POST',
  headers: {'{'}
    Authorization: 'Bearer ak_...',
    'Content-Type': 'application/json',
  {'}'},
  body: JSON.stringify({'{'} input: 'hello' {'}'}),
{'}'});</pre>
				</div>
			</div>
		</section>
	</div>
{/if}
