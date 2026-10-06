"""Maps a stored connection `type` string (e.g. "dynamiq.connections.OpenAI",
as stored in `console.connections.type` per PRD §8.2) to the matching Dynamiq
Connection + LLM classes, and instantiates them.

Only OpenAI is wired for real in this skeleton (used by
`app.services.runtime.run_playground_agent`); additional providers can be
added to `_CONNECTION_BUILDERS` following the same pattern once their
Connection/LLM classes are confirmed against the installed `dynamiq==0.65.0`.
"""
from collections.abc import Callable
from typing import Any

from dynamiq.components.embedders.base import BaseEmbedder
from dynamiq.components.embedders.openai import OpenAIEmbedder
from dynamiq.connections.connections import OpenAI as OpenAIConnection
from dynamiq.nodes.llms import BaseLLM
from dynamiq.nodes.llms import OpenAI as OpenAILLM


def _build_openai(config: dict[str, Any]) -> tuple[OpenAIConnection, BaseLLM]:
    api_key = config.get("api_key")
    kwargs: dict[str, Any] = {}
    # `is not None`, not truthiness - an explicit empty string must still be passed
    # through and rejected downstream, not silently treated as "not provided" and fall
    # back to OpenAIConnection's own default (the ambient OPENAI_API_KEY env var), which
    # has nothing to do with whatever connection this call is actually supposed to use.
    if api_key is not None:
        kwargs["api_key"] = api_key
    if config.get("url"):
        kwargs["url"] = config["url"]
    connection = OpenAIConnection(**kwargs)

    llm_kwargs: dict[str, Any] = {}
    if config.get("temperature") is not None:
        llm_kwargs["temperature"] = config["temperature"]
    if config.get("max_tokens") is not None:
        llm_kwargs["max_tokens"] = config["max_tokens"]
    llm = OpenAILLM(connection=connection, model=config.get("model", "gpt-4o-mini"), **llm_kwargs)
    return connection, llm


# Maps connection "type" strings (as they'll be stored on `console.connections.type`)
# to a builder returning (connection, llm).
_CONNECTION_BUILDERS: dict[str, Callable[[dict[str, Any]], tuple[Any, BaseLLM]]] = {
    "dynamiq.connections.OpenAI": _build_openai,
    "openai": _build_openai,
}

# Maps the same connection "type" strings to the dotted-path LLM class the compiler
# records in `compiled_definition` (PRD §6.1) — kept alongside _CONNECTION_BUILDERS so
# the two never drift apart as providers are added.
_LLM_TYPES: dict[str, str] = {
    "dynamiq.connections.OpenAI": "dynamiq.nodes.llms.OpenAI",
    "openai": "dynamiq.nodes.llms.OpenAI",
}


def build_llm(connection_type: str, config: dict[str, Any]) -> tuple[Any, BaseLLM]:
    """Build a (connection, llm) pair for `connection_type`.

    Raises `ValueError` for an unsupported/unknown connection type instead of
    guessing, so misconfigured agent specs fail loudly at compile/run time.
    """
    builder = _CONNECTION_BUILDERS.get(connection_type)
    if builder is None:
        raise ValueError(f"Unsupported connection type: {connection_type!r}")
    return builder(config)


def llm_type_for_connection_type(connection_type: str) -> str:
    """The dotted-path LLM class `build_llm(connection_type, ...)` would construct —
    for compiled_definition, without actually building a (connection, llm) pair."""
    llm_type = _LLM_TYPES.get(connection_type)
    if llm_type is None:
        raise ValueError(f"Unsupported connection type: {connection_type!r}")
    return llm_type


# --- Embedders (PRD §6.6 Knowledge Bases) ---
# Same single-provider-for-now shape as _CONNECTION_BUILDERS/build_llm above: only
# OpenAI is wired, following the identical dict-dispatch idiom so adding a provider
# later means adding one function + one dict entry, not touching callers.
def _build_openai_embedder(config: dict[str, Any]) -> tuple[OpenAIConnection, BaseEmbedder]:
    api_key = config.get("api_key")
    kwargs: dict[str, Any] = {}
    if api_key is not None:
        kwargs["api_key"] = api_key
    if config.get("url"):
        kwargs["url"] = config["url"]
    connection = OpenAIConnection(**kwargs)

    embedder = OpenAIEmbedder(connection=connection, model=config.get("model", "text-embedding-3-small"))
    return connection, embedder


_EMBEDDER_BUILDERS: dict[str, Callable[[dict[str, Any]], tuple[Any, BaseEmbedder]]] = {
    "dynamiq.connections.OpenAI": _build_openai_embedder,
    "openai": _build_openai_embedder,
}


def build_embedder(connection_type: str, config: dict[str, Any]) -> tuple[Any, BaseEmbedder]:
    """Build a (connection, embedder) pair for `connection_type` - the same component
    is used for both document and query embedding (`embed_documents`/`embed_text` and
    their `_async` twins on the returned `BaseEmbedder`), since OpenAI's embedding API
    doesn't distinguish the two the way some providers do.

    Raises `ValueError` for an unsupported/unknown connection type, same contract as
    `build_llm`.
    """
    builder = _EMBEDDER_BUILDERS.get(connection_type)
    if builder is None:
        raise ValueError(f"Unsupported connection type: {connection_type!r}")
    return builder(config)
