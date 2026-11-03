"""Turn-aware conversation memory trimming (PRD §6.2 Memory tab "Turn limit").

Dynamiq's own `Memory.get_agent_conversation()` (the one method `Agent.run()` calls,
`dynamiq/nodes/agents/base.py:1018`) trims by raw message count (`message_limit`), with
no notion of "turn" at all - a turn with a lot of tool calls/observations (save_mode=full)
can eat the whole budget, silently pushing earlier *complete* turns out while this one
turn is still only partially included. `_extract_valid_conversation` does avoid starting
mid-turn (it trims forward to the next USER message when the raw cut lands inside one),
but that still just drops whatever fraction of a turn didn't make the raw count - it does
not guarantee N whole turns.

`TurnAwareMemory` overrides that one method to group retrieved messages into turns (a
turn = one USER message plus everything up to, but not including, the next USER message -
reliable because every turn in this codebase's `agent_memory` writes starts with exactly
one USER message, never anything else) and keep the most recent `turn_limit` turns in
full, never a partial one. Everything else (`add`, `search`, `get_all`, the backend
connection) is inherited unchanged from `Memory` - this only replaces what gets fed into
an agent's prompt on retrieval, not how messages are stored.
"""
from dynamiq.memory.memory import Memory, MemoryRetrievalStrategy
from dynamiq.prompts import Message, MessageRole

# Generous ceiling on raw messages fetched per turn before grouping - must comfortably
# cover `turn_limit` turns even if every one of them maxes out `max_loops` (hardcoded to
# 8 for single-agent runs, app.services.runtime) worth of tool_call/tool_result pairs.
# Not exposed as a setting: this is just a safety margin for the raw fetch, not something
# an agent author should ever need to tune - turn_limit is the only knob they see.
_ESTIMATED_MAX_MESSAGES_PER_TURN = 30


class TurnAwareMemory(Memory):
    """`Memory` subclass that trims conversation retrieval by whole turns, not raw
    message count. See module docstring for why and exactly how.
    """

    turn_limit: int

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
        candidate_limit = max(turn_limit * _ESTIMATED_MAX_MESSAGES_PER_TURN, self.DEFAULT_LIMIT)

        # search() (not get_all() - see conversations.py's module docstring on why
        # get_all() must never be used here) returns newest-first; sort chronologically
        # for grouping, same ordering _extract_valid_conversation produces.
        raw_messages = self.search(query=None, filters=filters, limit=candidate_limit)
        raw_messages = sorted(raw_messages, key=lambda m: (m.metadata.get("timestamp", 0), m.metadata.get("sequence", 0)))

        turns = _group_into_turns(raw_messages)
        kept_turns = turns[-turn_limit:] if turn_limit > 0 else []
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
