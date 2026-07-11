from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.models import AIChatEvent, AIChatMessage, AIChatSession, AIChatTurn, AIUser
from app.services.chat_turn_recovery import reconcile_session_chat_state


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_stale_turn_becomes_failed_and_emits_one_terminal_sequence():
    engine, factory = await _database()
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=2)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message_id = uuid.uuid4()

    async with factory() as db:
        db.add(AIUser(id=user_id, email="stale@example.com"))
        db.add(AIChatSession(id=session_id, user_id=user_id, title="Stale turn"))
        db.add(AIChatTurn(
            request_id="request-stale",
            chat_session_id=session_id,
            user_id=user_id,
            status="active",
            cancel_requested=False,
            started_at=old,
            updated_at=old,
        ))
        db.add(AIChatMessage(
            id=message_id,
            chat_session_id=session_id,
            user_id=user_id,
            role="assistant",
            content="",
            metadata_json={"request_id": "request-stale", "status": "streaming"},
            created_at=old,
            updated_at=old,
        ))
        await db.commit()

        changed = await reconcile_session_chat_state(db, session_id, user_id, now - timedelta(seconds=45))
        await db.commit()
        assert changed == 1

        turn = (await db.execute(select(AIChatTurn))).scalar_one()
        message = (await db.execute(select(AIChatMessage).where(AIChatMessage.id == message_id))).scalar_one()
        events = list((await db.execute(select(AIChatEvent).order_by(AIChatEvent.id))).scalars().all())
        assert turn.status == "failed"
        assert message.metadata_json["status"] == "failed"
        assert message.metadata_json["error_type"] == "turn_interrupted"
        assert [event.event_type for event in events] == ["error", "turn.complete"]

        changed_again = await reconcile_session_chat_state(db, session_id, user_id, now - timedelta(seconds=45))
        await db.commit()
        events_again = list((await db.execute(select(AIChatEvent))).scalars().all())
        assert changed_again == 0
        assert len(events_again) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_completed_legacy_streaming_message_is_repaired_without_error():
    engine, factory = await _database()
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=2)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    message_id = uuid.uuid4()

    async with factory() as db:
        db.add(AIUser(id=user_id, email="legacy@example.com"))
        db.add(AIChatSession(id=session_id, user_id=user_id, title="Legacy turn"))
        db.add(AIChatMessage(
            id=message_id,
            chat_session_id=session_id,
            user_id=user_id,
            role="assistant",
            content="Lots Lots More",
            metadata_json={"request_id": "request-legacy", "status": "streaming"},
            created_at=old,
            updated_at=old,
        ))
        await db.commit()

        changed = await reconcile_session_chat_state(db, session_id, user_id, now - timedelta(seconds=45))
        await db.commit()

        message = (await db.execute(select(AIChatMessage).where(AIChatMessage.id == message_id))).scalar_one()
        events = list((await db.execute(select(AIChatEvent))).scalars().all())
        assert changed == 1
        assert message.content == "Lots Lots More"
        assert message.metadata_json["status"] == "completed"
        assert [event.event_type for event in events] == ["turn.complete"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_active_turn_with_persisted_answer_completes_without_error():
    engine, factory = await _database()
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=2)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async with factory() as db:
        db.add(AIUser(id=user_id, email="completed@example.com"))
        db.add(AIChatSession(id=session_id, user_id=user_id, title="Completed stale turn"))
        db.add(AIChatTurn(
            request_id="request-completed",
            chat_session_id=session_id,
            user_id=user_id,
            status="active",
            cancel_requested=False,
            started_at=old,
            updated_at=old,
        ))
        db.add(AIChatMessage(
            chat_session_id=session_id,
            user_id=user_id,
            role="assistant",
            content="The persisted answer.",
            metadata_json={"request_id": "request-completed", "status": "streaming"},
            created_at=old,
            updated_at=old,
        ))
        await db.commit()

        changed = await reconcile_session_chat_state(db, session_id, user_id, now - timedelta(seconds=45))
        await db.commit()

        turn = (await db.execute(select(AIChatTurn))).scalar_one()
        message = (await db.execute(select(AIChatMessage))).scalar_one()
        events = list((await db.execute(select(AIChatEvent))).scalars().all())
        assert changed == 1
        assert turn.status == "completed"
        assert message.metadata_json["status"] == "completed"
        assert [event.event_type for event in events] == ["turn.complete"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_live_turn_is_not_reconciled():
    engine, factory = await _database()
    now = datetime.now(timezone.utc)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async with factory() as db:
        db.add(AIUser(id=user_id, email="live@example.com"))
        db.add(AIChatSession(id=session_id, user_id=user_id, title="Live turn"))
        db.add(AIChatTurn(
            request_id="request-live",
            chat_session_id=session_id,
            user_id=user_id,
            status="active",
            cancel_requested=False,
            started_at=now,
            updated_at=now,
        ))
        await db.commit()

        changed = await reconcile_session_chat_state(db, session_id, user_id, now - timedelta(seconds=45))
        await db.commit()
        turn = (await db.execute(select(AIChatTurn))).scalar_one()
        assert changed == 0
        assert turn.status == "active"

    await engine.dispose()
