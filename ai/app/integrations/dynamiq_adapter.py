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

from dynamiq.connections.connections import OpenAI as OpenAIConnection
from dynamiq.nodes.llms import BaseLLM
from dynamiq.nodes.llms import OpenAI as OpenAILLM


def _build_openai(config: dict[str, Any]) -> tuple[OpenAIConnection, BaseLLM]:
    api_key = config.get("api_key")
    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if config.get("url"):
        kwargs["url"] = config["url"]
    connection = OpenAIConnection(**kwargs)
    llm = OpenAILLM(connection=connection, model=config.get("model", "gpt-4o-mini"))
    return connection, llm


# Maps connection "type" strings (as they'll be stored on `console.connections.type`)
# to a builder returning (connection, llm).
_CONNECTION_BUILDERS: dict[str, Callable[[dict[str, Any]], tuple[Any, BaseLLM]]] = {
    "dynamiq.connections.OpenAI": _build_openai,
    "openai": _build_openai,
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
