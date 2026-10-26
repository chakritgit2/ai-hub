<script lang="ts">
	import { onMount } from 'svelte';
	import { listSkillsForTenant, createSkill, updateSkill, publishSkill } from '$lib/api/console';
	import type { Skill } from '$lib/api/types';
	import { headerContent } from '$lib/stores/header';
	import { companyId, tenantId } from '$lib/stores/company';
	import { currentUser } from '$lib/stores/auth';

	// Pilot of "each menu shows only its own tenant's data" (option A) - pools skills
	// across every company under the selected tenant instead of the usual single
	// X-Company-Id company. See SkillsController::listSkills()'s X-Tenant-Id branch and
	// agents/+page.svelte, the first menu this pattern was applied to.

	let skills = $state<Skill[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showForm = $state(false);
	let editingId = $state<string | null>(null);
	// The company a pooled skill being edited belongs to - "+ New skill" has no row to
	// derive one from, so it falls back to whichever single company is currently
	// selected (CompanySwitcher), same as every other page's create flow.
	let editingCompanyId = $state<string | null>(null);

	let form = $state({ name: '', description: '', content: '' });
	let formError = $state<string | null>(null);
	let submitting = $state(false);
	let publishingId = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		// No tenant resolved yet (e.g. a brand new SSO user with zero company
		// memberships at all, so CompanySwitcher never has anything to pick) - nothing
		// to fetch, but still clear the spinner rather than spinning forever. Same guard
		// as agents/+page.svelte's load().
		if (!$tenantId) {
			skills = [];
			loading = false;
			return;
		}
		try {
			skills = await listSkillsForTenant($tenantId);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load skills.';
		} finally {
			loading = false;
		}
	}

	onMount(load);
	$effect(() => {
		$tenantId;
		load();
	});

	$effect(() => {
		headerContent.set({ menu: 'Skills', title: 'Skills', actions: newSkillAction });
	});

	function companyLabel(skillCompanyId: string): string | undefined {
		return $currentUser?.companies.find((c) => c.company_id === skillCompanyId)?.company_name;
	}

	// Every mutator below is X-Company-Id-scoped via the shared companyId store
	// (api/client.ts), but a pooled skill can belong to any company under the tenant -
	// point companyId at that skill's own company before acting, same approach as
	// connections/+page.svelte's withCompany().
	function withCompany<T>(skillCompanyId: string, fn: () => Promise<T>): Promise<T> {
		companyId.set(skillCompanyId);
		return fn();
	}

	function startCreate() {
		editingId = null;
		editingCompanyId = null;
		form = { name: '', description: '', content: '' };
		showForm = true;
	}

	function startEdit(skill: Skill) {
		editingId = skill.id;
		editingCompanyId = skill.company_id;
		form = {
			name: skill.name,
			description: skill.description ?? '',
			content: skill.latest_version.content
		};
		showForm = true;
	}

	async function submitForm() {
		submitting = true;
		formError = null;
		try {
			if (editingId === null) {
				await createSkill({ name: form.name, description: form.description || null, content: form.content });
			} else {
				await withCompany(editingCompanyId!, () =>
					updateSkill(editingId!, {
						name: form.name,
						description: form.description || null,
						content: form.content
					})
				);
			}
			showForm = false;
			await load();
		} catch (e) {
			formError = e instanceof Error ? e.message : 'Failed to save the skill.';
		} finally {
			submitting = false;
		}
	}

	async function publish(skill: Skill) {
		publishingId = skill.id;
		try {
			await withCompany(skill.company_id, () => publishSkill(skill.id));
			await load();
		} finally {
			publishingId = null;
		}
	}
</script>

{#snippet newSkillAction()}
	<button
		type="button"
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
		onclick={startCreate}
	>
		+ New skill
	</button>
{/snippet}

{#if showForm}
	<form
		class="mb-6 grid grid-cols-1 gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm"
		onsubmit={(e) => {
			e.preventDefault();
			submitForm();
		}}
	>
		{#if formError}
			<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
				{formError}
			</div>
		{/if}
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Name</span>
			<input
				type="text"
				bind:value={form.name}
				required
				disabled={editingId !== null}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm disabled:bg-neutral-50 disabled:text-neutral-500
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">Description</span>
			<input
				type="text"
				bind:value={form.description}
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			/>
		</label>
		<label class="block">
			<span class="text-sm font-medium text-neutral-700">SKILL.md content</span>
			<textarea
				bind:value={form.content}
				required
				rows="12"
				placeholder="# Skill name&#10;&#10;Instructions the agent follows when it loads this skill..."
				class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 font-mono text-sm
					focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
			></textarea>
			<span class="mt-1 block text-xs text-neutral-400">
				Max 100 KB (PRD §6.6a).
				{#if editingId !== null}
					Editing an already-published skill creates a new version — it never changes
					what's already published.
				{/if}
			</span>
		</label>
		<div class="flex gap-2">
			<button
				type="submit"
				disabled={submitting}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600"
			>
				{submitting ? 'Saving…' : 'Save skill'}
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
	<p class="text-sm text-neutral-500" role="status">Loading skills…</p>
{:else if error}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load skills</p>
		<p class="mt-1 text-sm text-neutral-700">{error}</p>
		<button
			type="button"
			class="mt-3 rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10"
			onclick={load}
		>
			Retry
		</button>
	</div>
{:else if skills.length === 0}
	<div class="rounded-lg border border-dashed border-neutral-300 bg-white p-8 text-center">
		<p class="text-sm text-neutral-500">
			No skills yet for this tenant — not yet loadable by an agent (CRUD only, PRD §6.6a).
		</p>
	</div>
{:else}
	<div class="overflow-x-auto rounded-lg border border-neutral-200 bg-white shadow-sm">
		<table class="min-w-full divide-y divide-neutral-200 text-sm">
			<thead class="bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500">
				<tr>
					<th class="px-4 py-2">Name</th>
					<th class="px-4 py-2">Company</th>
					<th class="px-4 py-2">Description</th>
					<th class="px-4 py-2">Version</th>
					<th class="px-4 py-2">Published</th>
					<th class="px-4 py-2"></th>
				</tr>
			</thead>
			<tbody class="divide-y divide-neutral-100">
				{#each skills as skill (skill.id)}
					<tr>
						<td class="px-4 py-2 font-medium text-neutral-900">{skill.name}</td>
						<td class="px-4 py-2 text-neutral-600">
							{#if companyLabel(skill.company_id)}
								<span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-500">
									{companyLabel(skill.company_id)}
								</span>
							{/if}
						</td>
						<td class="px-4 py-2 text-neutral-600">{skill.description ?? '—'}</td>
						<td class="px-4 py-2 font-mono text-xs text-neutral-600">v{skill.latest_version.version_no}</td>
						<td class="px-4 py-2 text-neutral-600">
							{skill.latest_version.is_published ? 'Yes' : 'Draft'}
						</td>
						<td class="px-4 py-2 text-right">
							<div class="flex items-center justify-end gap-2">
								<button
									type="button"
									class="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
									onclick={() => startEdit(skill)}
								>
									Edit
								</button>
								{#if !skill.latest_version.is_published}
									<button
										type="button"
										disabled={publishingId === skill.id}
										class="rounded-md bg-primary px-2 py-1 text-xs text-white hover:bg-primary-600"
										onclick={() => publish(skill)}
									>
										{publishingId === skill.id ? 'Publishing…' : 'Publish'}
									</button>
								{/if}
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
