"""Reconcile interrupted chat work into a durable, client-visible final state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIChatEvent, AIChatMessage, AIChatTurn


ACTIVE_MESSAGE_STATUSES = {"pending", "sending", "streaming", "tool_running"}
INTERRUPTED_ERROR_MESSAGE = "The response was interrupted before it finished. Please retry."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _request_id(message: AIChatMessage) -> str:
    metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
    value = metadata.get("request_id")
    return value if isinstance(value, str) else ""


def _message_is_complete(message: AIChatMessage) -> bool:
    metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
    return not metadata.get("failed") and bool((message.content or "").strip())


def _set_message_status(message: AIChatMessage, status: str, now: datetime) -> None:
    metadata: dict[str, Any] = dict(message.metadata_json or {})
    metadata["status"] = status
    metadata.pop("progress_context", None)
    if status == "failed":
        metadata.update({
            "failed": True,
            "error_type": "turn_interrupted",
            "error_message": INTERRUPTED_ERROR_MESSAGE,
        })
    else:
        metadata.pop("failed", None)
        metadata.pop("error_type", None)
        metadata.pop("error_message", None)
    message.metadata_json = metadata
    message.updated_at = now


async def _append_terminal_events(
    db: AsyncSession,
    *,
    session_id: UUID,
    user_id: UUID,
    request_id: str,
    failed: bool,
    now: datetime,
) -> None:
    if not request_id:
        return

    existing_result = await db.execute(
        select(AIChatEvent.event_type).where(
            AIChatEvent.chat_session_id == session_id,
            AIChatEvent.user_id == user_id,
            AIChatEvent.request_id == request_id,
            AIChatEvent.event_type.in_(["error", "turn.complete"]),
        )
    )
    existing = set(existing_result.scalars().all())
    created_at = now.isoformat()

    if failed and "error" not in existing:
        payload = {
            "type": "error",
            "request_id": request_id,
            "error_type": "turn_interrupted",
            "error_message": INTERRUPTED_ERROR_MESSAGE,
            "created_at": created_at,
        }
        db.add(AIChatEvent(
            chat_session_id=session_id,
            user_id=user_id,
            request_id=request_id,
            event_type="error",
            payload_json=jsonable_encoder(payload),
            created_at=now,
        ))

    if "turn.complete" not in existing:
        payload = {
            "type": "turn.complete",
            "request_id": request_id,
            "created_at": created_at,
        }
        db.add(AIChatEvent(
            chat_session_id=session_id,
            user_id=user_id,
            request_id=request_id,
            event_type="turn.complete",
            payload_json=jsonable_encoder(payload),
            created_at=now,
        ))


async def reconcile_session_chat_state(
    db: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    stale_before: datetime,
) -> int:
    """Finish stale leases and old streaming messages without replaying side effects."""
    now = _utcnow()
    changed = 0

    turn_result = await db.execute(
        select(AIChatTurn).where(
            AIChatTurn.chat_session_id == session_id,
            AIChatTurn.user_id == user_id,
            AIChatTurn.status == "active",
        ).with_for_update()
    )
    active_turns = list(turn_result.scalars().all())
    active_request_ids = [turn.request_id for turn in active_turns]
    stale_message_filter = and_(
        AIChatMessage.updated_at < stale_before,
        AIChatMessage.metadata_json["status"].as_string().in_(ACTIVE_MESSAGE_STATUSES),
    )
    message_filter = stale_message_filter
    if active_request_ids:
        message_filter = or_(
            stale_message_filter,
            AIChatMessage.metadata_json["request_id"].as_string().in_(active_request_ids),
        )
    message_result = await db.execute(
        select(AIChatMessage).where(
            AIChatMessage.chat_session_id == session_id,
            AIChatMessage.user_id == user_id,
            AIChatMessage.role == "assistant",
            message_filter,
        ).order_by(AIChatMessage.created_at.asc()).with_for_update()
    )
    messages = list(message_result.scalars().all())
    messages_by_request = {
        request_id: message
        for message in messages
        if (request_id := _request_id(message))
    }

    live_request_ids: set[str] = set()
    reconciled_request_ids: set[str] = set()

    for turn in active_turns:
        if _as_utc(turn.updated_at) >= stale_before:
            live_request_ids.add(turn.request_id)
            continue

        message = messages_by_request.get(turn.request_id)
        completed = bool(message and _message_is_complete(message))
        turn.status = "completed" if completed else "failed"
        turn.updated_at = now
        if message:
            _set_message_status(message, "completed" if completed else "failed", now)
        await _append_terminal_events(
            db,
            session_id=session_id,
            user_id=user_id,
            request_id=turn.request_id,
            failed=not completed,
            now=now,
        )
        reconciled_request_ids.add(turn.request_id)
        changed += 1

    for message in messages:
        metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
        if metadata.get("status") not in ACTIVE_MESSAGE_STATUSES:
            continue
        if _as_utc(message.updated_at) >= stale_before:
            continue

        request_id = _request_id(message)
        if request_id in live_request_ids or request_id in reconciled_request_ids:
            continue

        completed = _message_is_complete(message)
        _set_message_status(message, "completed" if completed else "failed", now)
        await _append_terminal_events(
            db,
            session_id=session_id,
            user_id=user_id,
            request_id=request_id,
            failed=not completed,
            now=now,
        )
        changed += 1

    if changed:
        await db.flush()
    return changed
