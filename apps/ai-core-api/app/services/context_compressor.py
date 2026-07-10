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
SUMMARY_VERSION = 1
DEFAULT_CONTEXT_WINDOW = 128_000
COMPRESSION_THRESHOLD = 0.75
TAIL_CONTEXT_SHARE = 0.30
MIN_TAIL_MESSAGES = 8
SUMMARY_MAX_TOKENS = 3_000
FALLBACK_SUMMARY_MAX_CHARS = 8_000

SummaryCall = Callable[[list[dict[str, Any]], int], Awaitable[dict[str, Any]]]


@dataclass
class ContextPreparation:
    messages: list[dict[str, Any]]
    summary_result: dict[str, Any] | None = None
    compacted: bool = False
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Conservative provider-neutral estimate used only for compression timing."""
    serialized = json.dumps(messages, ensure_ascii=False, default=str, separators=(",", ":"))
    return max(1, len(serialized) // 4 + len(messages) * 6)


def _prefix_hash(messages: list[dict[str, Any]], count: int) -> str:
    payload = json.dumps(messages[:count], ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _summary_message(summary: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": f"{SUMMARY_PREFIX}\n\n{summary.strip()}\n\n{SUMMARY_END}",
    }


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


def _fallback_summary(source: list[dict[str, Any]]) -> str:
    """Preserve bounded continuity if the auxiliary summarization request fails."""
    lines: list[str] = []
    for message in source:
        role = str(message.get("role") or "message").upper()
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    text = "\n\n".join(lines)
    if len(text) <= FALLBACK_SUMMARY_MAX_CHARS:
        return text
    half = FALLBACK_SUMMARY_MAX_CHARS // 2
    return text[:half].rstrip() + "\n\n[historical detail omitted]\n\n" + text[-half:].lstrip()


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
        summary = _fallback_summary(source)
        summary_result = None

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
