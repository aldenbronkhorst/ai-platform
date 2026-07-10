from __future__ import annotations

import uuid

import pytest

from app.services.context_compressor import SUMMARY_PREFIX, prepare_conversation_context


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self):
        self.id = uuid.uuid4()
        self.metadata_json = None


class FakeDb:
    def __init__(self, session):
        self.session = session
        self.flushed = False

    async def execute(self, _statement):
        return ScalarResult(self.session)

    async def flush(self):
        self.flushed = True


def conversation(message_count: int, chars: int) -> list[dict[str, str]]:
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"turn {index}: " + ("x" * chars),
        }
        for index in range(message_count)
    ]


@pytest.mark.asyncio
async def test_short_conversation_is_not_compacted():
    session = FakeSession()
    calls = []

    async def summarize(messages, max_tokens):
        calls.append((messages, max_tokens))
        return {"content": "unused"}

    messages = conversation(6, 100)
    result = await prepare_conversation_context(
        FakeDb(session),
        chat_session_id=session.id,
        messages=messages,
        system_prompt="system",
        tool_definitions=[],
        context_window=16_000,
        max_output_tokens=1_000,
        summarize=summarize,
    )

    assert result.messages == messages
    assert result.compacted is False
    assert calls == []


@pytest.mark.asyncio
async def test_long_conversation_persists_and_reuses_summary():
    session = FakeSession()
    db = FakeDb(session)
    calls = []

    async def summarize(messages, max_tokens):
        calls.append((messages, max_tokens))
        return {"content": "Verified historical facts and decisions.", "prompt_tokens": 50, "completion_tokens": 10}

    messages = conversation(24, 2_000)
    result = await prepare_conversation_context(
        db,
        chat_session_id=session.id,
        messages=messages,
        system_prompt="system",
        tool_definitions=[],
        context_window=16_000,
        max_output_tokens=1_000,
        summarize=summarize,
    )

    assert result.compacted is True
    assert result.estimated_tokens_after < result.estimated_tokens_before
    assert result.messages[0]["content"].startswith(SUMMARY_PREFIX)
    assert session.metadata_json["context_compaction"]["through_count"] > 0
    assert db.flushed is True
    assert len(calls) == 1

    calls.clear()
    reused = await prepare_conversation_context(
        db,
        chat_session_id=session.id,
        messages=messages,
        system_prompt="system",
        tool_definitions=[],
        context_window=64_000,
        max_output_tokens=1_000,
        summarize=summarize,
    )
    assert reused.messages[0]["content"].startswith(SUMMARY_PREFIX)
    assert calls == []


@pytest.mark.asyncio
async def test_changed_history_invalidates_saved_summary():
    session = FakeSession()
    db = FakeDb(session)

    async def summarize(_messages, _max_tokens):
        return {"content": "Summary"}

    messages = conversation(24, 2_000)
    await prepare_conversation_context(
        db,
        chat_session_id=session.id,
        messages=messages,
        system_prompt="system",
        tool_definitions=[],
        context_window=16_000,
        max_output_tokens=1_000,
        summarize=summarize,
    )
    previous_hash = session.metadata_json["context_compaction"]["through_hash"]
    messages[0] = {**messages[0], "content": "edited history"}

    await prepare_conversation_context(
        db,
        chat_session_id=session.id,
        messages=messages,
        system_prompt="system",
        tool_definitions=[],
        context_window=16_000,
        max_output_tokens=1_000,
        summarize=summarize,
    )
    assert session.metadata_json["context_compaction"]["through_hash"] != previous_hash
