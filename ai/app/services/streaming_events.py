"""Shared SSE event-shape parsing for real-time Agent streaming (PRD Goal #6).

Both `app.main_runtime` (Playground) and `app.main_gateway` (external API) consume the
same `StreamingEventMessage.data` shape dynamiq's `Agent.stream_response` produces when a
`stream_handler` is attached via `app.services.runtime.execute_agent_run` - written once
here instead of duplicated per app.
"""
import re
from typing import Any


def extract_answer_delta(event_data: Any) -> str | None:
    """Real dynamiq `Agent` streaming never hands a plain string to our callback, even in
    `StreamingMode.FINAL`: `AgentStreamingParserCallback._emit` builds a plain-string
    `content_to_stream` for the ANSWER step, but `Agent.stream_response` (dynamiq/nodes/
    agents/base.py) always re-wraps it as `StreamChunk(choices=[StreamChunkChoice(delta=
    StreamChunkChoiceDelta(content=..., step=...))]).model_dump()` before calling
    `run_on_node_execute_stream` - so `StreamingEventMessage.data` (built by
    `format_value(chunk)`, which preserves dict structure) is always this nested dict
    shape, never a bare string. Confirmed by reading the installed dynamiq==0.65.0 source
    directly, not assumed - an earlier version of this extraction checked
    `isinstance(event.data, str)`, which is never true for a real run and silently dropped
    every live token.
    """
    if not isinstance(event_data, dict):
        return None
    choices = event_data.get("choices") or []
    if not choices:
        return None
    delta = (choices[0] or {}).get("delta") or {}
    if delta.get("step") != "answer":
        return None
    content = delta.get("content")
    return content if isinstance(content, str) and content else None


def extract_tool_step_event(event_data: Any) -> dict[str, Any] | None:
    """Only ever returns something when the run was started with `StreamingMode.ALL`
    (see `execute_agent_run`'s `stream_mode` docstring) - `StreamingMode.FINAL`
    (Playground's own, unchanged, default) never emits either of the two `step` values
    this function looks for, so this is a no-op for every existing Playground caller.

    Confirmed by reading the installed dynamiq==0.65.0 source directly
    (`Agent._stream_agent_event`, `dynamiq/nodes/agents/agent.py`): both events reuse the
    exact same `{"choices": [{"delta": {"content": ..., "step": ...}}]}` wrapper
    `extract_answer_delta` already parses - only `content`'s shape differs (a dict here,
    never a bare string).

    Returns `{"phase": "tool_call", "tool": <name>}` right before a tool actually runs
    (dynamiq's structured `step="reasoning"` event - NOT the earlier plain-text
    `{"thought": "..."}` live token stream that shares the same `step` label but carries
    no `tool` key; that live chain-of-thought text is deliberately never surfaced here),
    or `{"phase": "tool_result", "tool": <name>, "sources": [...]}` once the tool
    finishes (`step="tool"`) - `sources` is only present when the tool's own result is
    shaped like dynamiq's TavilyTool/ExaTool default-mode output (see
    `_extract_tool_sources`), so a generic `kind: http` tool's differently-shaped result
    never risks a wrong/guessed "source" being shown. Returns None for everything else,
    including a `step="tool"` event whose tool carries no name at all (never actually
    happens for a real resolved tool, but guards against a malformed/unexpected payload
    rather than raising).
    """
    if not isinstance(event_data, dict):
        return None
    choices = event_data.get("choices") or []
    if not choices:
        return None
    delta = (choices[0] or {}).get("delta") or {}
    step = delta.get("step")
    content = delta.get("content")
    if not isinstance(content, dict):
        return None

    if step == "reasoning":
        tool = content.get("tool")
        if not isinstance(tool, dict) or not tool.get("name"):
            # The live {"thought": "..."} token stream shares step="reasoning" but has
            # no "tool" key - only the structured pre-tool-call event does.
            return None
        return {"phase": "tool_call", "tool": tool["name"]}

    if step == "tool":
        tool = content.get("tool") if isinstance(content.get("tool"), dict) else {}
        name = tool.get("name") or content.get("name")
        if not name:
            return None
        payload: dict[str, Any] = {"phase": "tool_result", "tool": name}
        sources = _extract_tool_sources(content.get("result"))
        if sources:
            payload["sources"] = sources
        return payload

    return None


_SOURCE_PATTERN = re.compile(r"^\[(.*?)\]\((.*?)\)")


def _extract_tool_sources(tool_result: Any) -> list[dict[str, str]] | None:
    """Only ever returns something for a tool result shaped exactly like dynamiq's own
    `TavilyTool`/`ExaTool` default-mode output - confirmed by reading the installed
    dynamiq==0.65.0 source directly: both tools' `_format_tavily_result`/
    `_format_search_response` return `{"content": {"sources_with_url": [...], ...}}`
    when `is_optimized_for_agents` is false (this project's default, never overridden),
    where each `sources_with_url` entry is a markdown link string `"[title](url)"`
    (Tavily appends an optional trailing `" (favicon: ...)"`, which the non-greedy regex
    below naturally ignores since it only matches up to the first `)`).

    Any other tool's result (a generic `kind: http` tool's arbitrary JSON, or even a
    Tavily/Exa result if `is_optimized_for_agents` were ever turned on, which this
    project never does) simply doesn't have this exact shape and yields None - never a
    best-effort guess at an unknown/different JSON structure, so a caller can never show
    a wrong "source" link.
    """
    if not isinstance(tool_result, dict):
        return None
    content = tool_result.get("content")
    if not isinstance(content, dict):
        return None
    raw_sources = content.get("sources_with_url")
    if not isinstance(raw_sources, list):
        return None

    sources = []
    for entry in raw_sources:
        if not isinstance(entry, str):
            continue
        match = _SOURCE_PATTERN.match(entry)
        if match:
            sources.append({"title": match.group(1), "url": match.group(2)})
    return sources or None
