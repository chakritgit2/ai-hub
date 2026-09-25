<script lang="ts">
	import { headerContent } from '$lib/stores/header';
	import { issueRuntimeToken } from '$lib/api/console';

	headerContent.set({ menu: 'Playground', title: 'Playground' });

	type RunState = 'done' | 'streaming';

	interface Message {
		role: 'user' | 'assistant';
		content: string;
		masked?: boolean;
		guardrail?: string;
		latencyMs?: number;
		costUsd?: number;
	}

	interface TraceStep {
		label: string;
		phase?: 2;
	}

	let messages = $state<Message[]>([]);
	let input = $state('');
	let runState = $state<RunState>('done');
	let conversationId = $state<string | null>(null);
	let streamTimer: ReturnType<typeof setInterval> | undefined;
	let tokenError = $state<string | null>(null);

	// Trace order per mockup screen 5: guardrail → thought → tool_call → tool_result →
	// retrieval → llm (primary/fallback) → output guardrail → final.
	const traceSteps: TraceStep[] = [
		{ label: 'input guardrail' },
		{ label: 'thought' },
		{ label: 'tool_call', phase: 2 },
		{ label: 'tool_result', phase: 2 },
		{ label: 'retrieval', phase: 2 },
		{ label: 'llm (primary)' },
		{ label: 'output guardrail' },
		{ label: 'final' }
	];

	const PII_PATTERN = /\b\d{10,13}\b/; // crude demo: Thai phone/national-ID-shaped digit runs

	function looksLikePii(text: string): boolean {
		return PII_PATTERN.test(text);
	}

	async function ensureConversation() {
		if (conversationId) return;
		conversationId = `conv_${Math.random().toString(36).slice(2, 10)}`;
		// A real Playground run first exchanges a 5-minute runtime token with console-api
		// (PRD §4.4-B) before opening SSE to ai-runtime; that call is demonstrated here even
		// though this skeleton does not implement the SSE client itself.
		try {
			await issueRuntimeToken('draft');
		} catch (e) {
			tokenError = e instanceof Error ? e.message : 'Could not issue a runtime token.';
		}
	}

	async function send() {
		const text = input.trim();
		if (!text || runState === 'streaming') return;

		await ensureConversation();

		const masked = looksLikePii(text);
		messages.push({ role: 'user', content: masked ? text.replace(PII_PATTERN, '••••••••') : text, masked });
		input = '';
		runState = 'streaming';

		const reply =
			'This is a simulated streaming reply — wire this panel up to POST /ai/v1/playground/stream once ai-runtime is available.';
		const assistantMessage: Message = { role: 'assistant', content: '' };
		messages.push(assistantMessage);
		const idx = messages.length - 1;

		let i = 0;
		streamTimer = setInterval(() => {
			i += 3;
			messages[idx] = { ...messages[idx], content: reply.slice(0, i) };
			if (i >= reply.length) {
				clearInterval(streamTimer);
				streamTimer = undefined;
				messages[idx] = {
					...messages[idx],
					content: reply,
					guardrail: 'output: passed',
					latencyMs: 842,
					costUsd: 0.0021
				};
				runState = 'done';
			}
		}, 40);
	}

	function stop() {
		if (streamTimer) {
			clearInterval(streamTimer);
			streamTimer = undefined;
		}
		const idx = messages.length - 1;
		if (idx >= 0 && messages[idx].role === 'assistant') {
			messages[idx] = { ...messages[idx], content: `${messages[idx].content} [stopped]` };
		}
		runState = 'done';
	}

	function newConversation() {
		messages = [];
		conversationId = null;
	}
</script>

<div class="grid h-full grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
	<div class="flex flex-col rounded-lg border border-neutral-200 bg-white shadow-sm">
		<div class="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
			<div class="text-sm">
				<span class="font-medium text-neutral-700">playground=true</span>
				{#if conversationId}
					<span class="ml-2 font-mono text-xs text-neutral-500">conversation_id: {conversationId}</span>
				{/if}
			</div>
			<button
				type="button"
				class="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:bg-neutral-50"
				onclick={newConversation}
			>
				New conversation
			</button>
		</div>

		{#if tokenError}
			<div class="border-b border-warning/30 bg-warning/10 px-4 py-2 text-sm text-warning-dark">
				Runtime token request failed (expected until console-api is running): {tokenError}
			</div>
		{/if}

		<div class="flex-1 space-y-3 overflow-y-auto p-4">
			{#if messages.length === 0}
				<p class="text-sm text-neutral-400">Send a message to start a Playground run.</p>
			{/if}
			{#each messages as message, i (i)}
				<div class="flex {message.role === 'user' ? 'justify-end' : 'justify-start'}">
					<div
						class="max-w-[75%] rounded-lg px-3 py-2 text-sm
							{message.role === 'user' ? 'bg-primary text-white' : 'bg-neutral-100 text-neutral-900'}"
					>
						<p class="whitespace-pre-wrap">{message.content}</p>
						{#if message.masked}
							<p class="mt-1 text-xs text-white/80">Sent to the model masked</p>
						{/if}
						{#if message.role === 'assistant' && message.guardrail}
							<p class="mt-1 text-xs text-neutral-500">
								{message.guardrail} · done · {message.latencyMs} ms · ${message.costUsd?.toFixed(4)}
							</p>
						{/if}
					</div>
				</div>
			{/each}
			{#if runState === 'streaming'}
				<p class="text-xs text-neutral-400" role="status">streaming…</p>
			{/if}
		</div>

		<form
			class="flex items-end gap-2 border-t border-neutral-200 p-4"
			onsubmit={(e) => {
				e.preventDefault();
				send();
			}}
		>
			<label class="flex-1">
				<span class="sr-only">Message</span>
				<textarea
					rows="2"
					bind:value={input}
					placeholder="Ask the agent something…"
					class="w-full resize-none rounded-md border border-neutral-300 px-3 py-2 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
				></textarea>
			</label>
			<button
				type="submit"
				disabled={runState === 'streaming' || !input.trim()}
				class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white shadow-sm
					hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50"
			>
				Send
			</button>
			<button
				type="button"
				disabled={runState !== 'streaming'}
				onclick={stop}
				class="rounded-md border border-danger/40 px-4 py-2 text-sm font-medium text-danger
					hover:bg-danger/10 disabled:cursor-not-allowed disabled:opacity-50"
			>
				Stop
			</button>
		</form>
	</div>

	<aside class="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm">
		<h2 class="text-sm font-semibold text-neutral-700">Trace</h2>
		<dl class="mt-3 grid grid-cols-2 gap-2 text-xs">
			<div><dt class="text-neutral-400">Latency</dt><dd class="text-neutral-800">842 ms</dd></div>
			<div><dt class="text-neutral-400">Tokens in/out</dt><dd class="text-neutral-800">— / —</dd></div>
			<div><dt class="text-neutral-400">Cost</dt><dd class="text-neutral-800">$0.0021</dd></div>
			<div><dt class="text-neutral-400">Loops</dt><dd class="text-neutral-800">1</dd></div>
		</dl>

		<ol class="mt-4 space-y-1">
			{#each traceSteps as step (step.label)}
				<li class="flex items-center justify-between rounded px-2 py-1 text-xs text-neutral-600">
					<span>{step.label}</span>
					{#if step.phase}
						<span class="rounded border border-neutral-300 px-1 text-[10px] text-neutral-400">P{step.phase}</span>
					{/if}
				</li>
			{/each}
		</ol>

		<div class="mt-4 flex flex-col gap-2">
			<button
				type="button"
				disabled
				title="Phase 3 — Evaluation"
				class="rounded-md border border-neutral-200 px-3 py-1.5 text-xs text-neutral-400"
			>
				+ Add as test case (phase 3)
			</button>
		</div>
	</aside>
</div>
