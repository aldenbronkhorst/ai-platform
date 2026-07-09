"""Shared connected-account state."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIConnectedAccount


async def effective_connected_accounts(
    db: AsyncSession,
    user_id: Optional[UUID],
    *,
    include_token_state: bool = False,
) -> list[AIConnectedAccount]:
    """Return connected-account rows for the authenticated user."""
    del include_token_state
    if not user_id:
        return []
    result = await db.execute(select(AIConnectedAccount).where(AIConnectedAccount.user_id == user_id))
    return list(result.scalars().all())
