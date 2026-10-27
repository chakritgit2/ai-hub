<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { EditorView, basicSetup } from 'codemirror';
	import { EditorState, Compartment } from '@codemirror/state';
	import { yaml } from '@codemirror/lang-yaml';

	let {
		value = $bindable(''),
		readOnly = false
	}: {
		value: string;
		readOnly?: boolean;
	} = $props();

	let container: HTMLDivElement;
	let view: EditorView | undefined;
	const readOnlyCompartment = new Compartment();

	onMount(() => {
		view = new EditorView({
			parent: container,
			state: EditorState.create({
				doc: value,
				extensions: [
					basicSetup,
					yaml(),
					readOnlyCompartment.of(EditorState.readOnly.of(readOnly)),
					EditorView.updateListener.of((update) => {
						if (update.docChanged) {
							value = update.state.doc.toString();
						}
					})
				]
			})
		});
	});

	onDestroy(() => {
		view?.destroy();
	});

	// `value` can also change from outside (loading a different version, "Start a new
	// version from it", etc.) - only push it into the editor when it actually differs from
	// the editor's own document, otherwise every keystroke's own round-trip through `value`
	// above would re-dispatch into CodeMirror and reset the cursor/undo history.
	$effect(() => {
		if (view && view.state.doc.toString() !== value) {
			view.dispatch({
				changes: { from: 0, to: view.state.doc.length, insert: value }
			});
		}
	});

	$effect(() => {
		view?.dispatch({
			effects: readOnlyCompartment.reconfigure(EditorState.readOnly.of(readOnly))
		});
	});
</script>

<div
	bind:this={container}
	class="yaml-editor mt-3 rounded-md border border-neutral-300 bg-neutral-50 font-mono text-xs
		focus-within:outline focus-within:outline-2 focus-within:outline-primary"
	class:opacity-50={readOnly}
></div>

<style>
	.yaml-editor :global(.cm-editor) {
		min-height: 20rem;
	}

	.yaml-editor :global(.cm-scroller) {
		font-family: inherit;
	}
</style>
