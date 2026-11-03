"""Unit tests for `TurnAwareMemory` (PRD §6.2 Memory tab "Turn limit"/"Context token
limit").

Pure in-process tests against Dynamiq's `InMemory` backend - no Postgres needed, since
the turn-grouping logic itself doesn't care which backend `search()`/`get_all()` actually
hits (see turn_aware_memory.py's module docstring: results are always re-sorted
chronologically after fetching, regardless of what order the backend returned them in).
`test_runtime_memory.py` covers the Postgres-backed, end-to-end "does MemorySpec.turn_limit/
token_limit actually reach the built agent" wiring instead.
"""
import pytest
from dynamiq.memory.backends.in_memory import InMemory
from dynamiq.memory.memory import MemorySaveMode
from dynamiq.prompts import MessageRole

from app.services.turn_aware_memory import TurnAwareMemory, _count_tokens, _group_into_turns

USER_ID = "company-x:hashed-user"
SESSION_ID = "conv-1"


def _memory(turn_limit: int | None = None, token_limit: int | None = None) -> TurnAwareMemory:
    return TurnAwareMemory(
        backend=InMemory(), save_mode=MemorySaveMode.FULL, turn_limit=turn_limit, token_limit=token_limit
    )


def _add_turn(memory: TurnAwareMemory, start_ts: float, roles_and_content: list[tuple[MessageRole, str]]) -> None:
    """Adds one turn's messages with strictly increasing timestamps 1 apart, same
    user_id/session_id metadata every real call site always sets."""
    for i, (role, content) in enumerate(roles_and_content):
        memory.add(
            role=role,
            content=content,
            metadata={"user_id": USER_ID, "session_id": SESSION_ID, "timestamp": start_ts + i},
        )


def _conversation(memory: TurnAwareMemory) -> list[tuple[MessageRole, str]]:
    messages = memory.get_agent_conversation(filters={"user_id": USER_ID, "session_id": SESSION_ID})
    return [(m.role, m.content) for m in messages]


def test_keeps_full_turns_including_a_tool_call_heavy_one_not_partial_messages():
    """The exact scenario turn_limit exists for: a turn with a lot of tool calls must
    never be split across the cutoff the way raw message_limit would split it."""
    memory = _memory(turn_limit=2)
    _add_turn(memory, 0, [(MessageRole.USER, "t1 q"), (MessageRole.ASSISTANT, "t1 a")])
    _add_turn(
        memory,
        10,
        [
            (MessageRole.USER, "t2 q"),
            (MessageRole.TOOL, "tool result 1"),
            (MessageRole.TOOL, "tool result 2"),
            (MessageRole.TOOL, "tool result 3"),
            (MessageRole.ASSISTANT, "t2 a"),
        ],
    )
    _add_turn(memory, 20, [(MessageRole.USER, "t3 q"), (MessageRole.ASSISTANT, "t3 a")])

    assert _conversation(memory) == [
        (MessageRole.USER, "t2 q"),
        (MessageRole.TOOL, "tool result 1"),
        (MessageRole.TOOL, "tool result 2"),
        (MessageRole.TOOL, "tool result 3"),
        (MessageRole.ASSISTANT, "t2 a"),
        (MessageRole.USER, "t3 q"),
        (MessageRole.ASSISTANT, "t3 a"),
    ]


def test_drops_the_oldest_whole_turn_first_fifo():
    memory = _memory(turn_limit=2)
    _add_turn(memory, 0, [(MessageRole.USER, "t1 q"), (MessageRole.ASSISTANT, "t1 a")])
    _add_turn(memory, 10, [(MessageRole.USER, "t2 q"), (MessageRole.ASSISTANT, "t2 a")])
    _add_turn(memory, 20, [(MessageRole.USER, "t3 q"), (MessageRole.ASSISTANT, "t3 a")])

    conversation = _conversation(memory)
    assert ("t1 q" in [c for _, c in conversation]) is False
    assert conversation == [
        (MessageRole.USER, "t2 q"),
        (MessageRole.ASSISTANT, "t2 a"),
        (MessageRole.USER, "t3 q"),
        (MessageRole.ASSISTANT, "t3 a"),
    ]


def test_turn_limit_larger_than_available_turns_returns_everything():
    memory = _memory(turn_limit=10)
    _add_turn(memory, 0, [(MessageRole.USER, "t1 q"), (MessageRole.ASSISTANT, "t1 a")])
    _add_turn(memory, 10, [(MessageRole.USER, "t2 q"), (MessageRole.ASSISTANT, "t2 a")])

    assert _conversation(memory) == [
        (MessageRole.USER, "t1 q"),
        (MessageRole.ASSISTANT, "t1 a"),
        (MessageRole.USER, "t2 q"),
        (MessageRole.ASSISTANT, "t2 a"),
    ]


def test_empty_memory_returns_empty_list():
    memory = _memory(turn_limit=5)
    assert _conversation(memory) == []


def test_token_limit_drops_oldest_turns_once_budget_exceeded():
    """The algorithm always starts from the most recent turn (t3) and adds older ones
    while they still fit - budget is sized to fit t3+t2 but not t3+t2+t1."""
    t1_tokens = _count_tokens("t1 q") + _count_tokens("t1 a")
    t2_tokens = _count_tokens("t2 q") + _count_tokens("t2 a")
    t3_tokens = _count_tokens("t3 q") + _count_tokens("t3 a")
    budget = t3_tokens + t2_tokens + t1_tokens - 1

    memory = _memory(token_limit=budget)
    _add_turn(memory, 0, [(MessageRole.USER, "t1 q"), (MessageRole.ASSISTANT, "t1 a")])
    _add_turn(memory, 10, [(MessageRole.USER, "t2 q"), (MessageRole.ASSISTANT, "t2 a")])
    _add_turn(memory, 20, [(MessageRole.USER, "t3 q"), (MessageRole.ASSISTANT, "t3 a")])

    conversation = _conversation(memory)
    assert ("t1 q" in [c for _, c in conversation]) is False
    assert conversation == [
        (MessageRole.USER, "t2 q"),
        (MessageRole.ASSISTANT, "t2 a"),
        (MessageRole.USER, "t3 q"),
        (MessageRole.ASSISTANT, "t3 a"),
    ]


def test_token_limit_always_keeps_the_most_recent_turn_even_if_it_alone_exceeds_budget():
    """An empty resumed context is worse than a slightly-over-budget one - same spirit as
    max_tokens not blocking a run that needs a bit more."""
    memory = _memory(token_limit=1)
    _add_turn(
        memory,
        0,
        [(MessageRole.USER, "a much longer question than the tiny token budget allows"), (MessageRole.ASSISTANT, "ok")],
    )

    conversation = _conversation(memory)
    assert conversation == [
        (MessageRole.USER, "a much longer question than the tiny token budget allows"),
        (MessageRole.ASSISTANT, "ok"),
    ]


def test_turn_limit_and_token_limit_combine_whichever_is_tighter_wins():
    """turn_limit alone would keep all 3 turns (limit=5) - token_limit is the one that
    actually bites here, proving the two combine rather than one silently overriding the
    other."""
    t1_tokens = _count_tokens("t1 q") + _count_tokens("t1 a")
    t2_tokens = _count_tokens("t2 q") + _count_tokens("t2 a")
    t3_tokens = _count_tokens("t3 q") + _count_tokens("t3 a")
    budget = t3_tokens + t2_tokens + t1_tokens - 1

    memory = TurnAwareMemory(backend=InMemory(), save_mode=MemorySaveMode.FULL, turn_limit=5, token_limit=budget)
    _add_turn(memory, 0, [(MessageRole.USER, "t1 q"), (MessageRole.ASSISTANT, "t1 a")])
    _add_turn(memory, 10, [(MessageRole.USER, "t2 q"), (MessageRole.ASSISTANT, "t2 a")])
    _add_turn(memory, 20, [(MessageRole.USER, "t3 q"), (MessageRole.ASSISTANT, "t3 a")])

    conversation = _conversation(memory)
    assert ("t1 q" in [c for _, c in conversation]) is False
    assert len(conversation) == 4


def test_requires_at_least_one_limit():
    with pytest.raises(ValueError):
        TurnAwareMemory(backend=InMemory(), save_mode=MemorySaveMode.FULL)


def test_group_into_turns_drops_a_leading_partial_group():
    """A raw fetch window can land mid-turn at the front (the candidate pool's own
    message_limit cutting into an even-earlier turn) - that leading partial group must be
    dropped, not kept as if it were a complete turn."""
    from dynamiq.prompts import Message

    messages = [
        Message(role=MessageRole.ASSISTANT, content="orphaned tail of a truncated turn"),
        Message(role=MessageRole.USER, content="t1 q"),
        Message(role=MessageRole.ASSISTANT, content="t1 a"),
    ]
    turns = _group_into_turns(messages)
    assert len(turns) == 1
    assert [m.content for m in turns[0]] == ["t1 q", "t1 a"]
