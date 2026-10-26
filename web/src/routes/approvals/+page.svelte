<script lang="ts">
	import { onMount } from 'svelte';
	import { listApprovalsForTenant, decideApproval } from '$lib/api/console';
	import type { Approval } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId, tenantId } from '$lib/stores/company';
	import { currentUser } from '$lib/stores/auth';

	// Pilot of "each menu shows only its own tenant's data" (option A) - pools approvals
	// (for companies the caller has write access in, same gate as the single-company
	// path's requireWriteRole()) across every company under the selected tenant instead of
	// the usual single X-Company-Id company. See
	// ApprovalsController::listApprovals()'s X-Tenant-Id branch and agents/+page.svelte,
	// the first menu this pattern was applied to.

	let approvals = $state<Approval[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let decidingId = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		// No tenant resolved yet (e.g. a brand new SSO user with zero company
		// memberships at all, so CompanySwitcher never has anything to pick) - nothing
		// to fetch, but still clear the spinner rather than spinning forever. Same guard
		// as agents/+page.svelte's load().
		if (!$tenantId) {
			approvals = [];
			loading = false;
			return;
		}
		try {
			approvals = await listApprovalsForTenant($tenantId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load approvals.';
		} finally {
			loading = false;
		}
	}

	// decideApproval is X-Company-Id-scoped via the shared companyId store (api/client.ts),
	// but a pooled approval can belong to any company under the tenant - point companyId
	// at that approval's own company before acting, same approach as
	// connections/+page.svelte's withCompany().
	async function decide(approval: Approval, decision: 'approved' | 'rejected') {
		if (decision === 'rejected' && !confirm('Reject this tool call? It will not run.')) return;
		decidingId = approval.id;
		try {
			companyId.set(approval.company_id);
			await decideApproval(approval.id, decision);
			await load();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to record the decision.';
		} finally {
			decidingId = null;
		}
	}

	onMount(load);
	$effect(() => {
		$tenantId;
		load();
	});

	function companyLabel(approvalCompanyId: string): string | undefined {
		return $currentUser?.companies.find((c) => c.company_id === approvalCompanyId)?.company_name;
	}

	$effect(() => {
		headerContent.set({ menu: 'Approvals', title: 'Approvals' });
	});
</script>

<p class="mb-4 text-sm text-neutral-600">
	`write`-access tools wait here for a human decision before they run (PRD §6.5). Approving
	lets the pending tool call through; rejecting blocks it.
</p>

{#if loading}
	<p class="text-sm text-neutral-500" role="status">Loading approvals…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load approvals</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if approvals.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">No approvals yet for this tenant.</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Company</th>
					<th class="px-4 py-2" title="The tool this pending call would invoke.">Tool</th>
					<th class="px-4 py-2" title="The agent run that requested this tool call.">Run</th>
					<th class="px-4 py-2" title="pending: waiting on a decision. expired: the decision window passed.">
						Status
					</th>
					<th class="px-4 py-2" title="Who (or what) decided this, once decided.">Decided by</th>
					<th class="px-4 py-2">Requested</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each approvals as approval (approval.id)}
					<tr class={approval.status !== 'pending' ? 'opacity-50' : ''}>
						<td class="px-4 py-2 text-neutral-600">
							{#if companyLabel(approval.company_id)}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
									{companyLabel(approval.company_id)}
								</span>
							{/if}
						</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-600">{approval.tool_id}</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-600">{approval.run_id}</td>
						<td class="px-4 py-2">
							<span
								class="text-xs font-medium capitalize"
								class:text-primary-700={approval.status === 'approved'}
								class:text-danger={approval.status === 'rejected'}
								class:text-neutral-500={approval.status === 'pending' || approval.status === 'expired'}
							>
								{approval.status}
							</span>
						</td>
						<td class="px-4 py-2 text-neutral-600">{approval.decided_by ?? '—'}</td>
						<td class="px-4 py-2 text-neutral-600">{new Date(approval.created_at).toLocaleString()}</td>
						<td class="px-4 py-2 text-right">
							{#if approval.status === 'pending'}
								<div class="flex justify-end gap-2">
									<button
										type="button"
										disabled={decidingId === approval.id}
										class="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-white shadow-sm hover:bg-primary-600"
										onclick={() => decide(approval, 'approved')}
									>
										{decidingId === approval.id ? 'Approving…' : 'Approve'}
									</button>
									<button
										type="button"
										disabled={decidingId === approval.id}
										class="rounded-md border border-danger/40 px-2.5 py-1 text-xs text-danger hover:bg-danger/10"
										onclick={() => decide(approval, 'rejected')}
									>
										{decidingId === approval.id ? 'Rejecting…' : 'Reject'}
									</button>
								</div>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
