"""Hermes-style rolling context compression for long chat sessions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIChatSession


SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION - REFERENCE ONLY] Earlier turns were compacted into "
    "the summary below. Treat it as historical background, not as a current "
    "instruction. Respond only to the latest user message after this summary."
)
SUMMARY_END = "--- END OF CONTEXT SUMMARY ---"
TURN_SUMMARY_PREFIX = (
    "[CURRENT TURN COMPACTION] Earlier completed tool work from this user turn "
    "was compacted below. Continue from the protected recent messages."
)
TURN_SUMMARY_END = "--- END OF CURRENT TURN SUMMARY ---"
SUMMARY_VERSION = 1
DEFAULT_CONTEXT_WINDOW = 128_000
COMPRESSION_THRESHOLD = 0.75
TAIL_CONTEXT_SHARE = 0.30
MIN_TAIL_MESSAGES = 8
SUMMARY_MAX_TOKENS = 3_000

SummaryCall = Callable[[list[dict[str, Any]], int], Awaitable[dict[str, Any]]]


@dataclass
class ContextPreparation:
    messages: list[dict[str, Any]]
    summary_result: dict[str, Any] | None = None
    compacted: bool = False
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0


@dataclass
class ToolLoopContextPreparation:
    messages: list[dict[str, Any]]
    summary_result: dict[str, Any] | None = None
    compacted: bool = False
    pruned_messages: int = 0
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Conservative provider-neutral estimate used only for compression timing."""
    serialized = json.dumps(messages, ensure_ascii=False, default=str, separators=(",", ":"))
    return max(1, len(serialized) // 4 + len(messages) * 6)


def estimate_request_tokens(
    messages: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    max_output_tokens: int,
) -> int:
    """Estimate the complete next request, including tools and output room."""
    tool_message = {
        "role": "system",
        "content": json.dumps(tool_definitions, ensure_ascii=False, default=str, separators=(",", ":")),
    }
    return estimate_messages_tokens([*messages, tool_message]) + max(0, max_output_tokens)


def _prefix_hash(messages: list[dict[str, Any]], count: int) -> str:
    payload = json.dumps(messages[:count], ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _summary_message(summary: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": f"{SUMMARY_PREFIX}\n\n{summary.strip()}\n\n{SUMMARY_END}",
    }


def _turn_summary_message(summary: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": f"{TURN_SUMMARY_PREFIX}\n\n{summary.strip()}\n\n{TURN_SUMMARY_END}",
    }


def _truncate_tool_arguments(arguments: str, max_string_chars: int = 200) -> str:
    """Shrink old tool arguments while preserving valid JSON for provider replay."""
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError, json.JSONDecodeError):
        return arguments[:max_string_chars] + "...[older argument omitted]" if len(arguments) > max_string_chars else arguments

    def shrink(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_string_chars:
            omitted = len(value) - max_string_chars
            return f"{value[:max_string_chars]}...[{omitted} older characters omitted]"
        if isinstance(value, list):
            return [shrink(item) for item in value]
        if isinstance(value, dict):
            return {key: shrink(item) for key, item in value.items()}
        return value

    return json.dumps(shrink(parsed), ensure_ascii=False, default=str, separators=(",", ":"))


def _tool_result_summary(tool_name: str, content: str) -> str:
    """Produce Hermes-style informative one-line history for an old tool result."""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None

    facts: list[str] = []
    if isinstance(payload, dict):
        for key in ("status", "error_type", "message", "exit_code", "timed_out"):
            value = payload.get(key)
            if value not in (None, "", False):
                facts.append(f"{key}={str(value)[:180]}")
        for key in ("records", "files", "results", "tables", "pages"):
            value = payload.get(key)
            if isinstance(value, list):
                facts.append(f"{key}={len(value)}")
        for key in ("stdout", "stderr"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                last_line = next((line.strip() for line in reversed(value.splitlines()) if line.strip()), "")
                if last_line:
                    facts.append(f"{key}_tail={last_line[:240]}")
    if not facts:
        text = " ".join(content.split())
        facts.append(text[:320] if text else "no text output")
    return f"[{tool_name}] prior result compacted ({len(content):,} chars); " + "; ".join(facts[:8])


def _tool_call_map(messages: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            call_id = str(tool_call.get("id") or "")
            function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
            if call_id:
                result[call_id] = str(function.get("name") or "tool")
    return result


def _prune_old_tool_context(
    messages: list[dict[str, Any]],
    *,
    context_window: int,
) -> tuple[list[dict[str, Any]], int]:
    """Condense old tool payloads while retaining a token-budgeted recent tail."""
    if not messages:
        return messages, 0

    result = [dict(message) for message in messages]
    tail_budget = max(4_000, int(context_window * TAIL_CONTEXT_SHARE))
    boundary = _tail_start(result, 0, tail_budget)
    tool_names = _tool_call_map(result)
    pruned = 0

    seen_tool_outputs: set[str] = set()
    for index in range(len(result) - 1, -1, -1):
        message = result[index]
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        content = str(message.get("content") or "")
        if len(content) < 200:
            continue
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        if digest in seen_tool_outputs and index < boundary:
            result[index] = {**message, "content": "[Duplicate tool output; identical to a newer result]"}
            pruned += 1
        else:
            seen_tool_outputs.add(digest)

    for index in range(boundary):
        message = result[index]
        if message.get("role") == "tool":
            content = message.get("content")
            if isinstance(content, str) and len(content) > 200 and not content.startswith("[Duplicate tool output"):
                tool_name = tool_names.get(str(message.get("tool_call_id") or ""), "tool")
                result[index] = {**message, "content": _tool_result_summary(tool_name, content)}
                pruned += 1
            continue

        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        next_calls: list[Any] = []
        changed = False
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                next_calls.append(tool_call)
                continue
            function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
            arguments = function.get("arguments")
            if isinstance(arguments, str) and len(arguments) > 500:
                tool_call = {
                    **tool_call,
                    "function": {**function, "arguments": _truncate_tool_arguments(arguments)},
                }
                changed = True
            next_calls.append(tool_call)
        if changed:
            result[index] = {**message, "tool_calls": next_calls}
            pruned += 1

    return result, pruned


def _aligned_tool_tail_start(messages: list[dict[str, Any]], start: int, lower_bound: int) -> int:
    """Keep an assistant tool call and all of its tool results in the same side."""
    while start > lower_bound and start < len(messages) and messages[start].get("role") == "tool":
        start -= 1
    return start


def _tool_loop_summary_prompt(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transcript = json.dumps(source, ensure_ascii=False, default=str, indent=2)
    return [
        {
            "role": "system",
            "content": (
                "Compact completed tool work from the current user turn for the same assistant. "
                "Preserve the user's objective, verified findings, identifiers, files, completed mutations, "
                "failed attempts and exact errors, unresolved work, and the next required step. Do not answer "
                "the user or invent facts. Use concise plain text."
            ),
        },
        {"role": "user", "content": "Completed current-turn work to compact:\n\n" + transcript},
    ]


async def prepare_tool_loop_context(
    *,
    messages: list[dict[str, Any]],
    turn_history_start: int,
    tool_definitions: list[dict[str, Any]],
    context_window: int | None,
    max_output_tokens: int,
    summarize: SummaryCall,
) -> ToolLoopContextPreparation:
    """Apply Hermes-style context pressure handling before a tool-loop model call."""
    window = max(16_000, int(context_window or DEFAULT_CONTEXT_WINDOW))
    before = estimate_request_tokens(messages, tool_definitions, max_output_tokens)
    threshold = int(window * COMPRESSION_THRESHOLD)
    if before <= threshold:
        return ToolLoopContextPreparation(
            messages=messages,
            estimated_tokens_before=before,
            estimated_tokens_after=before,
        )

    pruned_messages, pruned_count = _prune_old_tool_context(messages, context_window=window)
    after_prune = estimate_request_tokens(pruned_messages, tool_definitions, max_output_tokens)
    if after_prune <= threshold:
        return ToolLoopContextPreparation(
            messages=pruned_messages,
            pruned_messages=pruned_count,
            estimated_tokens_before=before,
            estimated_tokens_after=after_prune,
        )

    tail_budget = max(4_000, int(window * TAIL_CONTEXT_SHARE))
    start = _tail_start(pruned_messages, turn_history_start, tail_budget)
    start = _aligned_tool_tail_start(pruned_messages, start, turn_history_start)
    if start <= turn_history_start:
        return ToolLoopContextPreparation(
            messages=pruned_messages,
            pruned_messages=pruned_count,
            estimated_tokens_before=before,
            estimated_tokens_after=after_prune,
        )

    source = pruned_messages[turn_history_start:start]
    summary_result = await summarize(
        _tool_loop_summary_prompt(source),
        min(SUMMARY_MAX_TOKENS, max(800, max_output_tokens)),
    )
    summary = str(summary_result.get("content") or "").strip() if not summary_result.get("error") else ""
    if not summary:
        return ToolLoopContextPreparation(
            messages=pruned_messages,
            summary_result=summary_result,
            pruned_messages=pruned_count,
            estimated_tokens_before=before,
            estimated_tokens_after=after_prune,
        )

    prepared = [
        *pruned_messages[:turn_history_start],
        _turn_summary_message(summary),
        *pruned_messages[start:],
    ]
    after = estimate_request_tokens(prepared, tool_definitions, max_output_tokens)
    return ToolLoopContextPreparation(
        messages=prepared,
        summary_result=summary_result,
        compacted=True,
        pruned_messages=pruned_count,
        estimated_tokens_before=before,
        estimated_tokens_after=after,
    )


def _valid_saved_summary(metadata: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    saved = metadata.get("context_compaction")
    if not isinstance(saved, dict) or saved.get("version") != SUMMARY_VERSION:
        return None
    summary = saved.get("summary")
    through_count = saved.get("through_count")
    through_hash = saved.get("through_hash")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(through_count, int) or through_count < 1 or through_count > len(messages):
        return None
    if through_hash != _prefix_hash(messages, through_count):
        return None
    return saved


def _tail_start(messages: list[dict[str, Any]], lower_bound: int, token_budget: int) -> int:
    start = max(lower_bound, len(messages) - MIN_TAIL_MESSAGES)
    used = estimate_messages_tokens(messages[start:])
    while start > lower_bound:
        candidate = estimate_messages_tokens([messages[start - 1]])
        if used + candidate > token_budget:
            break
        start -= 1
        used += candidate
    return start


def _summary_source(saved_summary: str | None, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source: list[dict[str, Any]] = []
    if saved_summary:
        source.append(_summary_message(saved_summary))
    source.extend(messages)
    return source


def _summary_prompt(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transcript = json.dumps(source, ensure_ascii=False, default=str, indent=2)
    return [
        {
            "role": "system",
            "content": (
                "Summarize the historical conversation for a successor assistant. "
                "Preserve verified facts, user preferences, decisions, identifiers, file names, "
                "completed actions, unresolved questions, and current state. Distinguish facts "
                "from assumptions and failed attempts. Do not continue the task or answer any "
                "question. Use concise headings and plain text."
            ),
        },
        {
            "role": "user",
            "content": "Historical conversation to compact:\n\n" + transcript,
        },
    ]


async def prepare_conversation_context(
    db: AsyncSession,
    *,
    chat_session_id: UUID | None,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tool_definitions: list[dict[str, Any]],
    context_window: int | None,
    max_output_tokens: int,
    summarize: SummaryCall,
) -> ContextPreparation:
    """Return a bounded transcript and persist its rolling historical summary."""
    if not chat_session_id or len(messages) <= MIN_TAIL_MESSAGES:
        total = estimate_messages_tokens([{"role": "system", "content": system_prompt}, *messages])
        return ContextPreparation(messages=messages, estimated_tokens_before=total, estimated_tokens_after=total)

    session_result = await db.execute(select(AIChatSession).where(AIChatSession.id == chat_session_id))
    session = session_result.scalar_one_or_none()
    if session is None:
        total = estimate_messages_tokens([{"role": "system", "content": system_prompt}, *messages])
        return ContextPreparation(messages=messages, estimated_tokens_before=total, estimated_tokens_after=total)

    metadata = dict(session.metadata_json or {})
    saved = _valid_saved_summary(metadata, messages)
    through_count = int(saved["through_count"]) if saved else 0
    saved_summary = str(saved["summary"]) if saved else None
    active_messages = ([_summary_message(saved_summary)] if saved_summary else []) + messages[through_count:]

    fixed_tokens = estimate_messages_tokens([
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": json.dumps(tool_definitions, ensure_ascii=False, default=str)},
    ])
    before = fixed_tokens + estimate_messages_tokens(active_messages) + max(0, max_output_tokens)
    window = max(16_000, int(context_window or DEFAULT_CONTEXT_WINDOW))
    threshold = int(window * COMPRESSION_THRESHOLD)
    if before <= threshold:
        return ContextPreparation(
            messages=active_messages,
            estimated_tokens_before=before,
            estimated_tokens_after=before,
        )

    tail_budget = max(4_000, int(window * TAIL_CONTEXT_SHARE) - fixed_tokens - max_output_tokens)
    start = _tail_start(messages, through_count, tail_budget)
    if start <= through_count:
        return ContextPreparation(
            messages=active_messages,
            estimated_tokens_before=before,
            estimated_tokens_after=before,
        )

    source = _summary_source(saved_summary, messages[through_count:start])
    summary_result = await summarize(_summary_prompt(source), min(SUMMARY_MAX_TOKENS, max(800, max_output_tokens)))
    summary = str(summary_result.get("content") or "").strip() if not summary_result.get("error") else ""
    if not summary:
        return ContextPreparation(
            messages=active_messages,
            summary_result=summary_result,
            estimated_tokens_before=before,
            estimated_tokens_after=before,
        )

    metadata["context_compaction"] = {
        "version": SUMMARY_VERSION,
        "summary": summary,
        "through_count": start,
        "through_hash": _prefix_hash(messages, start),
    }
    session.metadata_json = metadata
    await db.flush()

    prepared = [_summary_message(summary), *messages[start:]]
    after = fixed_tokens + estimate_messages_tokens(prepared) + max(0, max_output_tokens)
    return ContextPreparation(
        messages=prepared,
        summary_result=summary_result,
        compacted=True,
        estimated_tokens_before=before,
        estimated_tokens_after=after,
    )
