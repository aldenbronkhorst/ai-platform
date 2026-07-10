from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.models import AIChatSession, AIChatTurn, AIUser
from app.routers.chat import _reserve_chat_turn


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
