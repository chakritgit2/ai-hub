<script lang="ts">
	import { page } from '$app/stores';
	import { get } from 'svelte/store';
	import { goto } from '$app/navigation';
	import { headerContent } from '$lib/stores/header';
	import { companyId } from '$lib/stores/company';
	import {
		createDatasetItem,
		issueRuntimeToken,
		listAgents,
		listAgentVersions,
		listDatasets,
		listRunSteps
	} from '$lib/api/console';
	import { cancelPlaygroundRun, getConversationMessages, streamPlaygroundRun } from '$lib/api/runtimeStream';
	import type {
		Agent,
		AgentVersion,
		Dataset,
		PlaygroundStreamApprovalRequiredEvent,
		PlaygroundStreamFinalEvent,
		RunStep
	} from '$lib/api/types';

	headerContent.set({ menu: 'Playground', title: 'Playground' });

	type RunState = 'done' | 'streaming';

	interface Message {
		role: 'user' | 'assistant';
		content: string;
		latencyMs?: number;
		costUsd?: number;
	}

	const STEP_TYPE_LABELS: Record<RunStep['step_type'], string> = {
		guardrail_input: 'Guardrail (input)',
		llm: 'LLM call',
		tool_call: 'Tool call',
		guardrail_output: 'Guardrail (output)'
	};

	let agents = $state<Agent[]>([]);
	let agentVersions = $state<AgentVersion[]>([]);
	let selectedAgentId = $state('');
	let selectedAgentVersionId = $state('');
	let pickerError = $state<string | null>(null);

	let messages = $state<Message[]>([]);
	let input = $state('');
	let runState = $state<RunState>('done');
	let conversationId = $state<string | null>(null);
	let runError = $state<string | null>(null);
	let lastResult = $state<PlaygroundStreamFinalEvent | null>(null);
	let pendingApproval = $state<PlaygroundStreamApprovalRequiredEvent | null>(null);
	let abortController: AbortController | undefined;

	// The run_id a run_started event hands us (main_runtime.py's runPlaygroundStream) - the
	// only way to later call the real POST /ai/v1/runs/{id}/cancel (Stop) or fetch this
	// run's real per-step trace via GET /runs/{id}/steps. The matching runtime token is kept
	// alongside it since cancelPlaygroundRun needs the same auth streamPlaygroundRun used.
	let currentRunId = $state<string | null>(null);
	let currentRuntimeToken: string | null = null;

	let steps = $state<RunStep[]>([]);
	let stepsError = $state<string | null>(null);
	let stepsLoading = $state(false);
	let loops = $derived(steps.filter((s) => s.step_type === 'llm').length);

	async function loadSteps(runId: string) {
		stepsLoading = true;
		stepsError = null;
		try {
			steps = await listRunSteps(runId);
		} catch (e) {
			stepsError = e instanceof Error ? e.message : 'Failed to load the trace.';
		} finally {
			stepsLoading = false;
		}
	}

	// "Add as test case" lets this turn's input be saved into an Evaluation dataset - the
	// input that started the latest turn, captured in send() rather than re-derived from
	// `messages` so it stays correct regardless of how the transcript is rendered later.
	let lastUserInput = $state('');

	let datasets = $state<Dataset[]>([]);
	let datasetsLoaded = $state(false);
	let datasetsError = $state<string | null>(null);
	let showAddTestCase = $state(false);
	let addTestCaseDatasetId = $state('');
	let addTestCaseQuestion = $state('');
	let addTestCaseAnswer = $state('');
	let addTestCaseError = $state<string | null>(null);
	let addTestCaseSaving = $state(false);
	let addTestCaseSaved = $state(false);

	async function openAddTestCase() {
		addTestCaseQuestion = lastUserInput;
		addTestCaseAnswer = lastResult?.output ?? '';
		addTestCaseError = null;
		addTestCaseSaved = false;
		showAddTestCase = true;
		if (!datasetsLoaded) {
			try {
				datasets = await listDatasets();
				datasetsLoaded = true;
			} catch (e) {
				datasetsError = e instanceof Error ? e.message : 'Failed to load datasets.';
			}
		}
	}

	async function saveTestCase() {
		if (!addTestCaseDatasetId || !addTestCaseQuestion.trim()) return;
		addTestCaseSaving = true;
		addTestCaseError = null;
		try {
			await createDatasetItem(addTestCaseDatasetId, {
				question: addTestCaseQuestion.trim(),
				expected_answer: addTestCaseAnswer.trim() || null
			});
			addTestCaseSaved = true;
		} catch (e) {
			addTestCaseError = e instanceof Error ? e.message : 'Failed to save the test case.';
		} finally {
			addTestCaseSaving = false;
		}
	}

	// Set once from the ?agentId=&agentVersionId=&input= query string a "Replay" link from
	// a run's detail page ([runs]/[id]/+page.svelte's openInPlayground()) arrives with -
	// consumed (and cleared) as soon as the matching agent/version shows up in the loaded
	// list, so a later manual agent switch is never silently overridden by a stale replay.
	let pendingAgentId = $state<string | null>(null);
	let pendingAgentVersionId = $state<string | null>(null);
	// A Replay link already brought its own input/intent - skip the next auto-resume so it
	// doesn't silently overwrite that with a leftover saved conversation instead.
	let suppressNextResume = false;

	async function loadAgents() {
		pickerError = null;
		try {
			agents = await listAgents();
			if (pendingAgentId && agents.some((a) => a.id === pendingAgentId)) {
				selectedAgentId = pendingAgentId;
			}
			pendingAgentId = null;
		} catch (e) {
			pickerError = e instanceof Error ? e.message : 'Failed to load agents.';
		}
	}

	let paramsConsumed = false;

	// Same "retry once the company switcher corrects a stale placeholder company_id"
	// pattern every list/detail page uses - see project-company-id-race-backlog. Also
	// clears any selected agent/version - a stale id from a previous company must never
	// survive a company switch (the agent-version cascade effect below clears
	// agentVersions/selectedAgentVersionId in response to selectedAgentId changing).
	//
	// The very first run also consumes ?agentId=&agentVersionId=&input= (a Replay link
	// from a run's detail page) instead of resetting selectedAgentId - combined into this
	// one effect, rather than a separate onMount, so there's no ordering race between two
	// independent reactive blocks both wanting to touch selectedAgentId on mount. Reads
	// `page` via get() (a plain one-off read), not the `$page` auto-subscription - the
	// latter would register `$page` as this effect's dependency, and the replaceState
	// goto() below would then re-trigger this very effect and immediately wipe the
	// selectedAgentId it had just set.
	$effect(() => {
		$companyId;
		if (!paramsConsumed) {
			paramsConsumed = true;
			const params = get(page).url.searchParams;
			pendingAgentId = params.get('agentId');
			pendingAgentVersionId = params.get('agentVersionId');
			const replayInput = params.get('input');
			if (replayInput) input = replayInput;
			if (pendingAgentId || pendingAgentVersionId || replayInput) {
				suppressNextResume = true;
				goto('/playground', { replaceState: true, keepFocus: true, noScroll: true });
			}
		} else {
			selectedAgentId = '';
		}
		loadAgents();
	});

	// Agent -> Agent Version cascade, same pattern as evaluation/new/+page.svelte.
	$effect(() => {
		const agentId = selectedAgentId;
		selectedAgentVersionId = '';
		agentVersions = [];
		if (!agentId) return;
		listAgentVersions(agentId)
			.then((versions) => {
				agentVersions = versions;
				if (pendingAgentVersionId && versions.some((v) => v.id === pendingAgentVersionId)) {
					selectedAgentVersionId = pendingAgentVersionId;
				}
				pendingAgentVersionId = null;
			})
			.catch((e) => {
				pickerError = e instanceof Error ? e.message : 'Failed to load agent versions.';
			});
	});

	// Resume this browser's last chat for this agent version on reload (GET
	// /ai/v1/conversations/{id}/messages) - the `messages` transcript above is only ever
	// in-memory, lost on refresh, even though the underlying conversation keeps working
	// server-side regardless. The saved conversation_id itself lives in localStorage (a
	// per-browser convenience, not authoritative - a 404 just means nothing to resume,
	// same as if nothing had been saved at all) keyed per company+agent version so
	// switching either never resumes the wrong thread.
	function conversationStorageKey(agentVersionId: string): string {
		return `playground:conversation:${get(companyId) ?? ''}:${agentVersionId}`;
	}

	function saveConversationId(agentVersionId: string, id: string | null) {
		try {
			const key = conversationStorageKey(agentVersionId);
			if (id) localStorage.setItem(key, id);
			else localStorage.removeItem(key);
		} catch {
			// private browsing / blocked storage - resuming is a convenience, never required.
		}
	}

	async function resumeConversation(agentVersionId: string) {
		let savedId: string | null = null;
		try {
			savedId = localStorage.getItem(conversationStorageKey(agentVersionId));
		} catch {
			return;
		}
		if (!savedId) return;

		try {
			const { token } = await issueRuntimeToken(agentVersionId);
			const resumed = await getConversationMessages(token, savedId);
			if (resumed.length > 0) {
				messages = resumed.map((m) => ({ role: m.role, content: m.content }));
				conversationId = savedId;
			}
		} catch {
			// Expired/swept/never-ours (404) or a transient failure - either way there's
			// nothing to resume right now; drop the stale pointer and start fresh silently,
			// same as a first-ever visit.
			saveConversationId(agentVersionId, null);
		}
	}

	$effect(() => {
		const versionId = selectedAgentVersionId;
		if (!versionId) return;
		if (suppressNextResume) {
			suppressNextResume = false;
			return;
		}
		// Only on a clean slate - never clobber an in-progress chat if this effect re-fires
		// for an unrelated reason while messages are already populated.
		if (messages.length === 0) {
			resumeConversation(versionId);
		}
	});

	async function send() {
		const text = input.trim();
		if (!text || runState === 'streaming') return;
		if (!selectedAgentVersionId) {
			runError = 'Select an agent and version first.';
			return;
		}

		runError = null;
		pendingApproval = null;
		messages.push({ role: 'user', content: text });
		input = '';
		runState = 'streaming';
		currentRunId = null;
		steps = [];
		stepsError = null;
		lastUserInput = text;
		showAddTestCase = false;

		const assistantMessage: Message = { role: 'assistant', content: '' };
		messages.push(assistantMessage);
		const idx = messages.length - 1;

		abortController = new AbortController();

		try {
			// A fresh 5-minute runtime token per turn - simpler than tracking expires_in
			// against wall-clock time for what's normally a cheap, infrequent call.
			const { token } = await issueRuntimeToken(selectedAgentVersionId);
			currentRuntimeToken = token;
			await streamPlaygroundRun(
				token,
				{ input: text, conversation_id: conversationId },
				{
					onRunStarted: (event) => {
						currentRunId = event.run_id;
					},
					onToken: (event) => {
						messages[idx] = { ...messages[idx], content: messages[idx].content + event.delta };
					},
					onFinal: (event) => {
						pendingApproval = null;
						conversationId = event.conversation_id ?? conversationId;
						lastResult = event;
						messages[idx] = {
							...messages[idx],
							content: event.output ?? messages[idx].content,
							latencyMs: event.latency_ms ?? undefined,
							costUsd: event.cost_usd ?? undefined
						};
						if (currentRunId) loadSteps(currentRunId);
						if (conversationId) saveConversationId(selectedAgentVersionId, conversationId);
					},
					onError: (event) => {
						pendingApproval = null;
						runError = event.detail ? `${event.error}: ${event.detail}` : event.error;
					},
					onApprovalRequired: (event) => {
						pendingApproval = event;
					}
				},
				abortController.signal
			);
		} catch (e) {
			const aborted = e instanceof DOMException && e.name === 'AbortError';
			if (!aborted) {
				runError = e instanceof Error ? e.message : 'Playground run failed.';
			}
		} finally {
			runState = 'done';
			pendingApproval = null;
			abortController = undefined;
		}
	}

	function stop() {
		// Cuts off the browser's own SSE read immediately, AND asks ai-runtime to cancel the
		// real in-flight run (PRD Goal #6) via the same runtime token this turn's stream used
		// - cooperative (see cancelPlaygroundRun's docstring), not a hard interrupt, but no
		// longer just a client-side abort that leaves the agent running server-side for
		// nothing. Fire-and-forget: a 404 (already finished) is a safe no-op.
		abortController?.abort();
		if (currentRunId && currentRuntimeToken) {
			cancelPlaygroundRun(currentRuntimeToken, currentRunId);
		}
		const idx = messages.length - 1;
		if (idx >= 0 && messages[idx].role === 'assistant') {
			messages[idx] = { ...messages[idx], content: `${messages[idx].content} [stopped]` };
		}
		runState = 'done';
		pendingApproval = null;
	}

	function newConversation() {
		// Must abort any in-flight run first - otherwise its onToken/onFinal closures keep
		// writing into `messages[idx]` of the array this function is about to replace,
		// corrupting the brand-new conversation with stale previous-conversation tokens.
		abortController?.abort();
		if (selectedAgentVersionId) saveConversationId(selectedAgentVersionId, null);
		runState = 'done';
		messages = [];
		conversationId = null;
		lastResult = null;
		runError = null;
		pendingApproval = null;
		currentRunId = null;
		currentRuntimeToken = null;
		steps = [];
		stepsError = null;
		lastUserInput = '';
		showAddTestCase = false;
	}
</script>

<div class="grid h-full grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
	<div class="flex flex-col rounded-lg border border-neutral-200 bg-white shadow-sm">
		<div class="flex flex-wrap items-center justify-between gap-3 border-b border-neutral-200 px-4 py-3">
			<div class="flex flex-wrap items-center gap-2">
				<select
					bind:value={selectedAgentId}
					disabled={runState === 'streaming'}
					class="rounded-md border border-neutral-300 px-2 py-1.5 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:bg-neutral-50"
				>
					<option value="" disabled>Select an agent</option>
					{#each agents as agent (agent.id)}
						<option value={agent.id}>{agent.display_name ?? agent.name}</option>
					{/each}
				</select>
				<select
					bind:value={selectedAgentVersionId}
					disabled={!selectedAgentId || agentVersions.length === 0 || runState === 'streaming'}
					class="rounded-md border border-neutral-300 px-2 py-1.5 text-sm
						focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:bg-neutral-50"
				>
					<option value="" disabled>
						{selectedAgentId ? 'Select a version' : 'Select an agent first'}
					</option>
					{#each agentVersions as version (version.id)}
						<option value={version.id}>v{version.version_no}{version.is_published ? ' (published)' : ' (draft)'}</option>
					{/each}
				</select>
				{#if conversationId}
					<span class="font-mono text-xs text-neutral-500">conversation_id: {conversationId}</span>
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

		{#if pickerError}
			<div class="border-b border-danger/30 bg-danger/10 px-4 py-2 text-sm text-danger">
				{pickerError}
			</div>
		{/if}
		{#if runError}
			<div class="border-b border-warning/30 bg-warning/10 px-4 py-2 text-sm text-warning-dark">
				{runError}
			</div>
		{/if}

		<div class="flex-1 space-y-3 overflow-y-auto p-4">
			{#if messages.length === 0}
				<p class="text-sm text-neutral-400">Select an agent version and send a message to start a Playground run.</p>
			{/if}
			{#each messages as message, i (i)}
				<div class="flex {message.role === 'user' ? 'justify-end' : 'justify-start'}">
					<div
						class="max-w-[75%] rounded-lg px-3 py-2 text-sm
							{message.role === 'user' ? 'bg-primary text-white' : 'bg-neutral-100 text-neutral-900'}"
					>
						<p class="whitespace-pre-wrap">{message.content}</p>
						{#if message.role === 'assistant' && message.latencyMs !== undefined}
							<p class="mt-1 text-xs text-neutral-500">
								done · {message.latencyMs} ms{message.costUsd !== undefined ? ` · $${message.costUsd.toFixed(4)}` : ''}
							</p>
						{/if}
					</div>
				</div>
			{/each}
			{#if pendingApproval}
				<p class="text-xs text-warning" role="status">
					กำลังรอการอนุมัติจากแอดมินสำหรับ tool: <span class="font-mono">{pendingApproval.tool}</span>
					(สูงสุด 5 นาที) — ไปที่หน้า <a href="/approvals" class="underline">Approvals</a> เพื่อกดอนุมัติ/ปฏิเสธ
				</p>
			{:else if runState === 'streaming'}
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
				disabled={runState === 'streaming' || !input.trim() || !selectedAgentVersionId}
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
			<div>
				<dt class="text-neutral-400">Latency</dt>
				<dd class="text-neutral-800">{lastResult?.latency_ms !== null && lastResult?.latency_ms !== undefined ? `${lastResult.latency_ms} ms` : '—'}</dd>
			</div>
			<div>
				<dt class="text-neutral-400">Tokens in/out</dt>
				<dd class="text-neutral-800">{lastResult?.tokens_in ?? '—'} / {lastResult?.tokens_out ?? '—'}</dd>
			</div>
			<div>
				<dt class="text-neutral-400">Cost</dt>
				<dd class="text-neutral-800">{lastResult?.cost_usd !== null && lastResult?.cost_usd !== undefined ? `$${lastResult.cost_usd.toFixed(4)}` : '—'}</dd>
			</div>
			<div>
				<dt class="text-neutral-400" title="Number of LLM call iterations the agent made to produce this run's answer.">
					Loops
				</dt>
				<dd class="text-neutral-800" title="Number of LLM call iterations the agent made to produce this run's answer.">
					{loops || '—'}
				</dd>
			</div>
		</dl>

		{#if stepsLoading}
			<p class="mt-4 text-xs text-neutral-400">Loading trace…</p>
		{:else if stepsError}
			<p class="mt-4 text-xs text-danger">{stepsError}</p>
		{:else if steps.length === 0}
			<p class="mt-4 text-xs text-neutral-400">
				{runState === 'streaming' ? 'Running…' : 'Send a message to see this run\'s trace.'}
			</p>
		{:else}
			<ol class="mt-4 space-y-1">
				{#each steps as step (step.id)}
					<li class="flex items-center justify-between rounded px-2 py-1 text-xs">
						<span
							class="truncate {step.status === 'error' || step.status === 'block'
								? 'text-danger'
								: 'text-neutral-600'}"
						>
							{STEP_TYPE_LABELS[step.step_type]}{step.name ? ` · ${step.name}` : ''}
						</span>
						{#if step.latency_ms !== null}
							<span class="shrink-0 text-neutral-400">{step.latency_ms} ms</span>
						{/if}
					</li>
				{/each}
			</ol>
			{#if currentRunId}
				<a href="/runs/{currentRunId}" class="mt-2 inline-block text-xs text-primary underline">
					View full trace
				</a>
			{/if}
		{/if}

		<div class="mt-4 flex flex-col gap-2">
			{#if !showAddTestCase}
				<button
					type="button"
					disabled={!lastResult}
					title={lastResult ? 'Save this turn to an Evaluation dataset' : 'Send a message first'}
					onclick={openAddTestCase}
					class="rounded-md border border-neutral-300 px-3 py-1.5 text-xs text-neutral-700
						hover:bg-neutral-50 disabled:cursor-not-allowed disabled:border-neutral-200 disabled:text-neutral-400"
				>
					+ Add as test case
				</button>
			{:else}
				<div class="flex flex-col gap-2 rounded-md border border-neutral-200 p-3">
					<h3 class="text-xs font-semibold text-neutral-700">Add as test case</h3>

					{#if datasetsError}
						<p class="text-xs text-danger">{datasetsError}</p>
					{:else if !datasetsLoaded}
						<p class="text-xs text-neutral-400">Loading datasets…</p>
					{:else if datasets.length === 0}
						<p class="text-xs text-neutral-400">
							No datasets yet — create one on the <a href="/datasets" class="underline">Datasets</a> page first.
						</p>
					{:else}
						<label class="flex flex-col gap-1">
							<span class="text-xs text-neutral-500">Dataset</span>
							<select
								bind:value={addTestCaseDatasetId}
								class="rounded-md border border-neutral-300 px-2 py-1 text-xs
									focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
							>
								<option value="" disabled>Select a dataset</option>
								{#each datasets as dataset (dataset.id)}
									<option value={dataset.id}>{dataset.name}</option>
								{/each}
							</select>
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs text-neutral-500">Question</span>
							<textarea
								bind:value={addTestCaseQuestion}
								rows="2"
								class="w-full resize-none rounded-md border border-neutral-300 px-2 py-1 text-xs
									focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
							></textarea>
						</label>
						<label class="flex flex-col gap-1">
							<span class="text-xs text-neutral-500">Expected answer (optional - edit before saving)</span>
							<textarea
								bind:value={addTestCaseAnswer}
								rows="3"
								class="w-full resize-none rounded-md border border-neutral-300 px-2 py-1 text-xs
									focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
							></textarea>
						</label>
					{/if}

					{#if addTestCaseError}
						<p class="text-xs text-danger">{addTestCaseError}</p>
					{/if}
					{#if addTestCaseSaved}
						<p class="text-xs text-success" role="status">Saved.</p>
					{/if}

					<div class="flex justify-end gap-2">
						<button
							type="button"
							onclick={() => (showAddTestCase = false)}
							class="rounded-md border border-neutral-300 px-3 py-1 text-xs text-neutral-700 hover:bg-neutral-50"
						>
							{addTestCaseSaved ? 'Close' : 'Cancel'}
						</button>
						{#if !addTestCaseSaved}
							<button
								type="button"
								disabled={addTestCaseSaving || !addTestCaseDatasetId || !addTestCaseQuestion.trim()}
								onclick={saveTestCase}
								class="rounded-md bg-primary px-3 py-1 text-xs font-medium text-white shadow-sm
									hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50"
							>
								{addTestCaseSaving ? 'Saving…' : 'Save'}
							</button>
						{/if}
					</div>
				</div>
			{/if}
		</div>
	</aside>
</div>
