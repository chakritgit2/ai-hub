<script lang="ts">
	import { headerContent } from '$lib/stores/header';
	import { currentUser, roleForCompany } from '$lib/stores/auth';
	import { companyId } from '$lib/stores/company';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let isAdmin = $derived(roleForCompany($currentUser, $companyId) === 'admin');

	$effect(() => {
		headerContent.set({
			menu: 'Workflows',
			title: data.version
				? `v${data.version.version_no} — ${data.workflow?.name ?? 'Workflow'}`
				: 'Version',
			actions: headerActions
		});
	});
</script>

{#snippet headerActions()}
	<a
		href={data.workflow ? `/workflows/${data.workflow.id}` : '/workflows'}
		class="rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium
			text-neutral-700 shadow-sm hover:bg-neutral-50"
	>
		Back to workflow
	</a>
{/snippet}

{#if data.loadError}
	<div class="rounded-md border border-danger/30 bg-danger/5 p-4">
		<p class="text-sm font-medium text-danger">Could not load this version</p>
		<p class="mt-1 text-sm text-neutral-700">{data.loadError}</p>
	</div>
{:else if !data.version}
	<p class="text-sm text-neutral-500">Version not found.</p>
{:else}
	<div class="mb-4 rounded-md border border-neutral-300 bg-neutral-50 p-3 text-sm text-neutral-600">
		Read-only — v{data.version.version_no} is
		{data.version.is_published ? 'published and immutable' : 'a past draft'}.
	</div>

	<section class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<h2 class="text-sm font-semibold text-neutral-700">Spec</h2>
		<pre class="mt-2 overflow-x-auto rounded-md bg-neutral-50 p-3 text-xs text-neutral-800">{JSON.stringify(
				data.version.spec,
				null,
				2
			)}</pre>
	</section>

	{#if isAdmin && data.version.compiled_definition}
		<section class="mt-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
			<h2 class="text-sm font-semibold text-neutral-700">Advanced: compiled definition</h2>
			<pre class="mt-2 overflow-x-auto rounded-md bg-neutral-900 p-3 text-xs text-neutral-100">{JSON.stringify(
					data.version.compiled_definition,
					null,
					2
				)}</pre>
		</section>
	{/if}
{/if}
