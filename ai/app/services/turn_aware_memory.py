"""Turn-aware conversation memory trimming (PRD §6.2 Memory tab "Turn limit"/"Context
token limit").

Dynamiq's own `Memory.get_agent_conversation()` (the one method `Agent.run()` calls,
`dynamiq/nodes/agents/base.py:1018`) trims by raw message count (`message_limit`), with
no notion of "turn" at all - a turn with a lot of tool calls/observations (save_mode=full)
can eat the whole budget, silently pushing earlier *complete* turns out while this one
turn is still only partially included. `_extract_valid_conversation` does avoid starting
mid-turn (it trims forward to the next USER message when the raw cut lands inside one),
but that still just drops whatever fraction of a turn didn't make the raw count - it does
not guarantee N whole turns, and it never looks at token size at all (a single huge tool
result can blow the real LLM context window regardless of how few messages/turns it is).

`TurnAwareMemory` overrides that one method to group retrieved messages into turns (a
turn = one USER message plus everything up to, but not including, the next USER message -
reliable because every turn in this codebase's `agent_memory` writes starts with exactly
one USER message, never anything else) and keep the most recent turns in full, never a
partial one, under two independent, combinable caps:

- `turn_limit`: at most this many turns.
- `token_limit`: at most this many estimated tokens total (tiktoken's `cl100k_base`, the
  same "rough, not provider-exact" estimate `app.services.eval_runner` already uses for
  eval cost estimation - counts message content only, not the system prompt/tool
  definitions/the current turn's own input, so it bounds *memory's* contribution to the
  context, not the full request).

Whichever cap is tighter wins for any given older turn; the single most recent turn is
always kept regardless of its own size (an empty resumed context is worse than a
slightly-over-budget one - same spirit as `max_tokens` not blocking a run that needs a
bit more). Everything else (`add`, `search`, `get_all`, the backend connection) is
inherited unchanged from `Memory` - this only replaces what gets fed into an agent's
prompt on retrieval, not how messages are stored.
"""
from functools import lru_cache

import tiktoken
from pydantic import model_validator

from dynamiq.memory.memory import Memory, MemoryRetrievalStrategy
from dynamiq.prompts import Message, MessageRole

# Generous ceiling on raw messages fetched per turn before grouping - must comfortably
# cover the turns actually needed even if every one of them maxes out `max_loops`
# (hardcoded to 8 for single-agent runs, app.services.runtime) worth of tool_call/
# tool_result pairs. Not exposed as a setting - just a safety margin for the raw fetch.
_ESTIMATED_MAX_MESSAGES_PER_TURN = 30
# Used only to size the raw-fetch candidate pool when turn_limit itself is unset (a
# token_limit-only config still needs *some* assumed turn count to fetch enough raw
# messages to trim from) - never a real cap, that's token_limit's job in that case.
_ASSUMED_TURN_CEILING_WHEN_UNSET = 50


@lru_cache
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def _turn_token_estimate(turn: list[Message]) -> int:
    return sum(_count_tokens(message.content or "") for message in turn)


class TurnAwareMemory(Memory):
    """`Memory` subclass that trims conversation retrieval by whole turns (by count
    and/or estimated token size), not raw message count. See module docstring for why
    and exactly how.
    """

    turn_limit: int | None = None
    token_limit: int | None = None

    @model_validator(mode="after")
    def _require_at_least_one_limit(self) -> "TurnAwareMemory":
        if self.turn_limit is None and self.token_limit is None:
            raise ValueError("TurnAwareMemory requires turn_limit and/or token_limit to be set")
        return self

    def get_agent_conversation(
        self,
        query: str | None = None,
        limit: int | None = None,
        filters: dict | None = None,
        strategy: MemoryRetrievalStrategy = MemoryRetrievalStrategy.ALL,
    ) -> list[Message]:
        # Turn-grouping only has a defined meaning for "give me the most recent turns" -
        # RELEVANT/BOTH (semantic search against the query) aren't a thing this codebase
        # configures today (no `retrieval_strategy` field exists on MemorySpec), but fall
        # back to the base class's own behavior rather than silently ignoring the query
        # if that ever changes.
        if strategy != MemoryRetrievalStrategy.ALL:
            return super().get_agent_conversation(query=query, limit=limit, filters=filters, strategy=strategy)

        turn_limit = limit if limit is not None else self.turn_limit
        turn_ceiling_for_fetch = turn_limit if turn_limit is not None else _ASSUMED_TURN_CEILING_WHEN_UNSET
        candidate_limit = max(turn_ceiling_for_fetch * _ESTIMATED_MAX_MESSAGES_PER_TURN, self.DEFAULT_LIMIT)

        # search() (not get_all() - see conversations.py's module docstring on why
        # get_all() must never be used here) returns newest-first; sort chronologically
        # for grouping, same ordering _extract_valid_conversation produces.
        raw_messages = self.search(query=None, filters=filters, limit=candidate_limit)
        raw_messages = sorted(raw_messages, key=lambda m: (m.metadata.get("timestamp", 0), m.metadata.get("sequence", 0)))

        turns = _group_into_turns(raw_messages)
        kept_turns = _select_turns_within_budget(turns, turn_limit, self.token_limit)
        return [message for turn in kept_turns for message in turn]


def _group_into_turns(messages: list[Message]) -> list[list[Message]]:
    """Splits a chronological message list into turns - a new turn starts at every USER
    message. Drops a leading group that doesn't start with USER: that means the raw fetch
    window cut into an earlier turn mid-way, so it's a partial turn, not a complete one
    (same "always start clean" guarantee Dynamiq's own _extract_valid_conversation gives,
    applied per turn-group here instead of per message).
    """
    turns: list[list[Message]] = []
    for message in messages:
        if message.role == MessageRole.USER or not turns:
            turns.append([])
        turns[-1].append(message)

    if turns and turns[0][0].role != MessageRole.USER:
        turns = turns[1:]

    return turns


def _select_turns_within_budget(
    turns: list[list[Message]], turn_limit: int | None, token_limit: int | None
) -> list[list[Message]]:
    """Walks from the most recent turn backward, keeping whole turns while both caps
    (whichever are set) allow it. The most recent turn is always included even if it
    alone exceeds token_limit - see module docstring for why."""
    selected: list[list[Message]] = []
    token_total = 0

    for turn in reversed(turns):
        if turn_limit is not None and len(selected) >= turn_limit:
            break
        if token_limit is not None:
            turn_tokens = _turn_token_estimate(turn)
            if selected and token_total + turn_tokens > token_limit:
                break
            token_total += turn_tokens
        selected.append(turn)

    selected.reverse()
    return selected
