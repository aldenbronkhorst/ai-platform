import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app.routers.chat import _finish_database_read
from app.services.chat_event_stream import ChatEventPersistenceError, ChatEventWriter, _coalesce


def test_coalesce_joins_only_adjacent_raw_deltas():
    events = _coalesce([
        ("reasoning.delta", {"text": "Check "}),
        ("reasoning.delta", {"text": "Odoo"}),
        ("tool.start", {"id": "tool-1"}),
        ("reasoning.delta", {"text": "After "}),
        ("reasoning.delta", {"text": "tool"}),
    ])

    assert events == [
        ("reasoning.delta", {"text": "Check Odoo"}),
        ("tool.start", {"id": "tool-1"}),
        ("reasoning.delta", {"text": "After tool"}),
    ]


@pytest.mark.asyncio
async def test_database_read_finishes_cleanup_before_stream_cancellation_propagates():
    started = asyncio.Event()
    finished = asyncio.Event()

    async def database_read():
        started.set()
        try:
            await asyncio.sleep(0.01)
            return ["event"]
        finally:
            finished.set()

    task = asyncio.create_task(_finish_database_read(database_read()))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_writer_persists_ordered_events_with_request_metadata():
    stored = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add_all(self, rows):
            stored.extend(rows)

        async def commit(self):
            return None

    class FakeSessionFactory:
        def __call__(self):
            return FakeSession()

    session_id = uuid.uuid4()
    user_id = uuid.uuid4()
    writer = ChatEventWriter(
        session_id,
        user_id,
        "request-1",
        session_factory=FakeSessionFactory(),
    )
    writer.start()
    writer.emit("message.delta", {"text": "Lots "})
    writer.emit("message.delta", {"text": "Lots More"})
    writer.emit("tool.start", {"id": "tool-1", "name": "workspace"})
    message_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    writer.emit("message.complete", {"id": message_id, "created_at": created_at})
    await writer.close()

    assert [row.event_type for row in stored] == ["message.delta", "tool.start", "message.complete"]
    assert stored[0].payload_json["text"] == "Lots Lots More"
    assert stored[0].payload_json["request_id"] == "request-1"
    assert stored[0].chat_session_id == session_id
    assert stored[0].user_id == user_id
    assert stored[2].payload_json["id"] == str(message_id)
    assert stored[2].payload_json["created_at"] == created_at.isoformat()


@pytest.mark.asyncio
async def test_writer_retries_a_transient_database_failure(monkeypatch):
    monkeypatch.setattr("app.services.chat_event_stream.PERSIST_RETRY_DELAYS", (0, 0, 0))
    attempts = 0
    stored = []

    class FakeSession:
        def __init__(self):
            self.rows = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add_all(self, rows):
            self.rows = rows

        async def commit(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary outage")
            stored.extend(self.rows)

    class FakeSessionFactory:
        def __call__(self):
            return FakeSession()

    writer = ChatEventWriter(uuid.uuid4(), uuid.uuid4(), "request-retry", session_factory=FakeSessionFactory())
    writer.start()
    writer.emit("message.delta", {"text": "hello"})
    await writer.close()

    assert attempts == 2
    assert [row.event_type for row in stored] == ["message.delta"]


@pytest.mark.asyncio
async def test_writer_surfaces_a_permanent_database_failure(monkeypatch):
    monkeypatch.setattr("app.services.chat_event_stream.PERSIST_RETRY_DELAYS", (0, 0, 0))
    attempts = 0

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add_all(self, _rows):
            return None

        async def commit(self):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("database unavailable")

    class FakeSessionFactory:
        def __call__(self):
            return FakeSession()

    writer = ChatEventWriter(uuid.uuid4(), uuid.uuid4(), "request-fail", session_factory=FakeSessionFactory())
    writer.start()
    writer.emit("message.delta", {"text": "hello"})

    with pytest.raises(ChatEventPersistenceError):
        await writer.close()
    assert attempts == 3
