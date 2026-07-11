"""Durable, ordered chat events shared by every connected client."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import AsyncSessionLocal
from app.models.models import AIChatEvent

logger = logging.getLogger(__name__)

DELTA_EVENT_TYPES = {"message.delta", "reasoning.delta"}
PERSIST_RETRY_DELAYS = (0.0, 0.1, 0.5)


class ChatEventPersistenceError(RuntimeError):
    """Raised when an ordered event batch cannot be committed."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _event_payload(event_type: str, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    event = dict(payload)
    event["type"] = event_type
    event.setdefault("request_id", request_id)
    event.setdefault("created_at", _utcnow().isoformat())
    return jsonable_encoder(event)


def _coalesce(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """Join adjacent raw deltas without changing event order or interpreting text."""
    result: list[tuple[str, dict[str, Any]]] = []
    for event_type, payload in events:
        if event_type in DELTA_EVENT_TYPES and result and result[-1][0] == event_type:
            previous = result[-1][1]
            previous_text = previous.get("text")
            current_text = payload.get("text")
            if isinstance(previous_text, str) and isinstance(current_text, str):
                previous["text"] = previous_text + current_text
                continue
        result.append((event_type, dict(payload)))
    return result


class ChatEventWriter:
    """Buffers provider callbacks and commits ordered event batches to PostgreSQL."""

    def __init__(
        self,
        chat_session_id: UUID,
        user_id: UUID,
        request_id: str,
        *,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    ) -> None:
        self.chat_session_id = chat_session_id
        self.user_id = user_id
        self.request_id = request_id
        self._session_factory = session_factory
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        if self._task is None:
            raise RuntimeError("ChatEventWriter.start() must be called before emit().")
        self._queue.put_nowait((event_type, _event_payload(event_type, payload or {}, self.request_id)))

    def emit_agent_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "status.update")
        self.emit(event_type, event)

    async def close(self) -> None:
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            first = await self._queue.get()
            if first is None:
                return

            batch = [first]
            stopping = False
            await asyncio.sleep(0.025)
            while len(batch) < 128:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is None:
                    stopping = True
                    break
                batch.append(item)

            events = _coalesce(batch)
            await self._persist(events)

            if stopping:
                return

    async def _persist(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        last_error: Exception | None = None
        for attempt, delay in enumerate(PERSIST_RETRY_DELAYS, start=1):
            if delay:
                await asyncio.sleep(delay)
            rows = [
                AIChatEvent(
                    chat_session_id=self.chat_session_id,
                    user_id=self.user_id,
                    request_id=self.request_id,
                    event_type=event_type,
                    payload_json=payload,
                    created_at=_utcnow(),
                )
                for event_type, payload in events
            ]
            try:
                async with self._session_factory() as db:
                    db.add_all(rows)
                    await db.commit()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Chat event persistence attempt failed | session=%s request=%s count=%d attempt=%d",
                    self.chat_session_id,
                    self.request_id,
                    len(rows),
                    attempt,
                    exc_info=True,
                )

        raise ChatEventPersistenceError(
            f"Could not persist {len(events)} chat events for request {self.request_id}."
        ) from last_error
