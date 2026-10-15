<script lang="ts">
	import { goto } from '$app/navigation';
	import { createWorkflow } from '$lib/api/console';
	import { headerContent } from '$lib/stores/header';

	let form = $state({ name: '', description: '' });
	let error = $state<string | null>(null);
	let saving = $state(false);

	$effect(() => {
		headerContent.set({ menu: 'Workflows', title: 'New workflow' });
	});

	async function submit() {
		error = null;
		if (!form.name.trim()) {
			error = 'Please enter a name.';
			return;
		}
		saving = true;
		try {
			const workflow = await createWorkflow({
				name: form.name,
				description: form.description || null
			});
			await goto(`/workflows/${workflow.id}`);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to create the workflow.';
			saving = false;
		}
	}
</script>

<form
	class="max-w-lg space-y-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm"
	onsubmit={(e) => {
		e.preventDefault();
		submit();
	}}
>
	{#if error}
		<div class="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>
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
		<span class="text-sm font-medium text-neutral-700">Description</span>
		<textarea
			bind:value={form.description}
			rows="3"
			class="mt-1 w-full rounded-md border border-neutral-300 px-3 py-2 text-sm
				focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
		></textarea>
	</label>

	<button
		type="submit"
		disabled={saving}
		class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-primary-600 disabled:opacity-50"
	>
		{saving ? 'Creating…' : 'Create workflow'}
	</button>
</form>
