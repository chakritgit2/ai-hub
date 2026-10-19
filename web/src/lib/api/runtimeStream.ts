// Direct-to-ai-runtime client for POST /ai/v1/playground/stream (PRD §4.4-B, §9.3).
//
// Unlike console.ts's apiFetch, this never goes through console-api: the browser opens
// the SSE connection straight to ai-runtime, authenticated with the short-lived runtime
// token from issueRuntimeToken(), not the SSO session token or a company header. The
// browser's native EventSource can't be used here since it only supports GET with no
// custom headers/body - this does a plain streaming fetch + manual SSE framing instead.

import { PUBLIC_AI_RUNTIME_BASE_URL } from '$env/static/public';
import type { PlaygroundStreamErrorEvent, PlaygroundStreamFinalEvent, PlaygroundStreamTokenEvent } from './types';

export interface PlaygroundStreamRequest {
	input: string;
	conversation_id?: string | null;
}

export interface PlaygroundStreamHandlers {
	onToken: (event: PlaygroundStreamTokenEvent) => void;
	onFinal: (event: PlaygroundStreamFinalEvent) => void;
	onError: (event: PlaygroundStreamErrorEvent) => void;
}

/**
 * Opens the stream, dispatches each SSE event to the matching handler, and resolves once
 * the response body ends (after `final` or `error`, or if the connection is aborted via
 * `signal`). Throws if the initial HTTP request itself fails (non-2xx or no body) - a
 * mid-stream failure is instead delivered as an `error` event by the server.
 */
export async function streamPlaygroundRun(
	runtimeToken: string,
	request: PlaygroundStreamRequest,
	handlers: PlaygroundStreamHandlers,
	signal?: AbortSignal
): Promise<void> {
	const base = (PUBLIC_AI_RUNTIME_BASE_URL ?? '').replace(/\/$/, '');
	const response = await fetch(`${base}/playground/stream`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Accept: 'text/event-stream',
			Authorization: `Bearer ${runtimeToken}`
		},
		body: JSON.stringify(request),
		signal
	});

	if (!response.ok || !response.body) {
		const detail = await response.text().catch(() => '');
		throw new Error(`playground stream request failed (${response.status}): ${detail || response.statusText}`);
	}

	const reader = response.body.getReader();
	const decoder = new TextDecoder();
	// sse-starlette writes CRLF between events/fields, not plain "\n" - normalize so
	// framing below only ever has to deal with "\n". Done on the whole accumulated buffer
	// (not per freshly-decoded chunk) because a chunk boundary can land between the "\r"
	// and "\n" of one terminator - normalizing only the new piece would miss that split
	// pair and leave a stray "\r" in the reconstructed buffer.
	let buffer = '';

	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		buffer = buffer.replace(/\r\n/g, '\n');

		let separatorIndex: number;
		while ((separatorIndex = buffer.indexOf('\n\n')) !== -1) {
			const rawEvent = buffer.slice(0, separatorIndex);
			buffer = buffer.slice(separatorIndex + 2);
			dispatchEvent(rawEvent, handlers);
		}
	}
}

function dispatchEvent(rawEvent: string, handlers: PlaygroundStreamHandlers): void {
	let eventType = 'message';
	const dataLines: string[] = [];
	for (const line of rawEvent.split('\n')) {
		if (line.startsWith('event:')) {
			eventType = line.slice('event:'.length).trim();
		} else if (line.startsWith('data:')) {
			dataLines.push(line.slice('data:'.length).trim());
		}
	}
	if (dataLines.length === 0) return;

	const payload = JSON.parse(dataLines.join('\n'));
	if (eventType === 'token') handlers.onToken(payload);
	else if (eventType === 'final') handlers.onFinal(payload);
	else if (eventType === 'error') handlers.onError(payload);
}
