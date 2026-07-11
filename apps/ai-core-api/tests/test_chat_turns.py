from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.models import AIChatMessage, AIChatSession, AIChatTurn, AIUser
from app.routers.chat import ChatMessageCreate, _claim_next_chat_turn, _prepare_chat_turn, _reserve_chat_turn


@pytest.mark.asyncio
async def test_only_one_active_turn_can_be_reserved_per_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with factory() as db:
        db.add(AIUser(id=user_id, email="turn@example.com"))
        db.add(AIChatSession(id=session_id, user_id=user_id, title="Turn test"))
        await db.commit()

        first, created = await _reserve_chat_turn(db, session_id, user_id, "request-1")
        assert created is True
        assert first.status == "active"

        same, created = await _reserve_chat_turn(db, session_id, user_id, "request-1")
        assert created is False
        assert same.request_id == "request-1"

        with pytest.raises(HTTPException) as exc_info:
            await _reserve_chat_turn(db, session_id, user_id, "request-2")
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error_type"] == "turn_already_active"

        first.status = "completed"
        await db.commit()
        second, created = await _reserve_chat_turn(db, session_id, user_id, "request-2")
        assert created is True
        assert second.status == "active"

    await engine.dispose()


@pytest.mark.asyncio
async def test_turn_messages_are_persisted_before_background_execution():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with factory() as db:
        db.add(AIUser(id=user_id, email="prepared@example.com"))
        db.add(AIChatSession(id=session_id, user_id=user_id, title="Prepared turn"))
        await db.commit()

        await _reserve_chat_turn(db, session_id, user_id, "request-prepared")
        user_message_id, assistant_message_id = await _prepare_chat_turn(
            db,
            session_id,
            ChatMessageCreate(content="Can you check this?"),
            "request-prepared",
            user_id,
        )
        await db.commit()

        messages = list((await db.execute(
            select(AIChatMessage).where(AIChatMessage.chat_session_id == session_id).order_by(AIChatMessage.created_at)
        )).scalars().all())
        assert [message.id for message in messages] == [user_message_id, assistant_message_id]
        assert messages[0].content == "Can you check this?"
        assert messages[1].metadata_json["status"] == "streaming"

    await engine.dispose()


@pytest.mark.asyncio
async def test_committed_turn_is_claimed_exactly_once(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.routers.chat.AsyncSessionLocal", factory)

    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    request = ChatMessageCreate(content="Run this after the request commits")
    async with factory() as db:
        db.add(AIUser(id=user_id, email="queue@example.com"))
        db.add(AIChatSession(id=session_id, user_id=user_id, title="Queue test"))
        await db.commit()
        turn, _created = await _reserve_chat_turn(db, session_id, user_id, "request-queue")
        user_message_id, assistant_message_id = await _prepare_chat_turn(
            db,
            session_id,
            request,
            "request-queue",
            user_id,
        )
        turn.request_payload_json = request.model_dump(mode="json")
        turn.user_message_id = user_message_id
        turn.assistant_message_id = assistant_message_id
        await db.commit()

    claimed = await _claim_next_chat_turn()
    assert claimed is not None
    assert claimed["request_id"] == "request-queue"
    assert claimed["request"]["content"] == "Run this after the request commits"
    assert await _claim_next_chat_turn() is None

    async with factory() as db:
        turn = (await db.execute(select(AIChatTurn).where(AIChatTurn.request_id == "request-queue"))).scalar_one()
        assert turn.attempt_count == 1
        assert turn.lease_owner
        assert turn.lease_expires_at

    await engine.dispose()
