from __future__ import annotations

import uuid

import pytest

from app.services.context_compressor import (
    SUMMARY_PREFIX,
    TURN_SUMMARY_PREFIX,
    prepare_conversation_context,
    prepare_tool_loop_context,
)


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


@pytest.mark.asyncio
async def test_summary_failure_preserves_the_original_history():
    session = FakeSession()
    messages = conversation(24, 2_000)

    async def summarize(_messages, _max_tokens):
        return {"error": True, "error_type": "rate_limit_exceeded", "content": ""}

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
    assert session.metadata_json is None


@pytest.mark.asyncio
async def test_tool_loop_prunes_old_payloads_before_the_next_model_call():
    calls = []

    async def summarize(messages, max_tokens):
        calls.append((messages, max_tokens))
        return {"content": "unused"}

    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "do the work"}]
    for index in range(18):
        messages.extend([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": "workspace", "arguments": '{"code":"' + ("x" * 1600) + '"}'},
                }],
            },
            {
                "role": "tool",
                "tool_call_id": f"call-{index}",
                "content": '{"status":"success","stdout":"' + ("y" * 1800) + '"}',
            },
        ])

    result = await prepare_tool_loop_context(
        messages=messages,
        turn_history_start=2,
        tool_definitions=[{"type": "function", "function": {"name": "workspace"}}],
        context_window=16_000,
        max_output_tokens=1_000,
        summarize=summarize,
    )

    assert result.pruned_messages > 0
    assert result.estimated_tokens_after < result.estimated_tokens_before


@pytest.mark.asyncio
async def test_tool_loop_summarizes_completed_current_turn_work_when_pruning_is_not_enough():
    calls = []

    async def summarize(messages, max_tokens):
        calls.append((messages, max_tokens))
        return {"content": "Verified 18 completed steps; continue with the remaining records."}

    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "complete all records"}]
    for index in range(18):
        messages.extend([
            {"role": "assistant", "content": f"Completed step {index}. " + ("detail " * 350)},
            {"role": "tool", "tool_call_id": f"call-{index}", "content": "ok"},
        ])

    result = await prepare_tool_loop_context(
        messages=messages,
        turn_history_start=2,
        tool_definitions=[],
        context_window=16_000,
        max_output_tokens=1_000,
        summarize=summarize,
    )

    assert result.compacted is True
    assert result.messages[2]["content"].startswith(TURN_SUMMARY_PREFIX)
    assert result.estimated_tokens_after < result.estimated_tokens_before
    assert len(calls) == 1
