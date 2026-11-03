"""Unit tests for `TurnAwareMemory` (PRD §6.2 Memory tab "Turn limit").

Pure in-process tests against Dynamiq's `InMemory` backend - no Postgres needed, since
the turn-grouping logic itself doesn't care which backend `search()`/`get_all()` actually
hits (see turn_aware_memory.py's module docstring: results are always re-sorted
chronologically after fetching, regardless of what order the backend returned them in).
`test_runtime_memory.py` covers the Postgres-backed, end-to-end "does MemorySpec.turn_limit
actually reach the built agent" wiring instead.
"""
from dynamiq.memory.backends.in_memory import InMemory
from dynamiq.memory.memory import MemorySaveMode
from dynamiq.prompts import MessageRole

from app.services.turn_aware_memory import TurnAwareMemory, _group_into_turns

USER_ID = "company-x:hashed-user"
SESSION_ID = "conv-1"


def _memory(turn_limit: int) -> TurnAwareMemory:
    return TurnAwareMemory(backend=InMemory(), save_mode=MemorySaveMode.FULL, turn_limit=turn_limit)


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
