"""Shared connected-account state for delegated connector tokens."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIConnectedAccount
from app.services.token_storage import token_secret_name, token_status, token_status_from_data


DELEGATED_TOKEN_PROVIDERS = {"azure", "github"}
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_account(db: AsyncSession, user_id: UUID, provider: str) -> Optional[AIConnectedAccount]:
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == provider,
        )
    )
    return result.scalar_one_or_none()


def effective_delegated_status(account: Optional[AIConnectedAccount], token_state: dict[str, Any]) -> str:
    status = token_state.get("status") or "not_connected"
    if status != "connected":
        return status
    if account and account.status == "error":
        return "error"
    return "connected"


def _token_username(token_data: dict[str, Any], fallback: str) -> str:
    return (
        token_data.get("username")
        or token_data.get("login")
        or token_data.get("provider_username")
        or fallback
    )


def _delegated_account_view(
    account: Optional[AIConnectedAccount],
    provider: str,
    user_id: UUID,
    token_state: dict[str, Any],
    status: str,
) -> AIConnectedAccount:
    view = AIConnectedAccount(
        id=account.id if account else None,
        user_id=user_id,
        provider=provider,
        provider_user_id=account.provider_user_id if account else None,
        provider_username=(
            account.provider_username
            if account and account.provider_username
            else _token_username(token_state, f"{provider}-user")
        ),
        provider_display_name=account.provider_display_name if account else None,
        scopes=token_state.get("scope") or (account.scopes if account else None),
        status=status,
        secret_reference=(
            account.secret_reference
            if account and account.secret_reference
            else token_secret_name(provider, user_id)
        ),
        target_environment=account.target_environment if account else "production",
        permission_summary=account.permission_summary if account else None,
        last_verified_at=account.last_verified_at if account else None,
        disconnected_at=account.disconnected_at if account else None,
    )
    view.token_status = token_state.get("status") or "unknown"
    view.token_expires_on = token_state.get("expires_on")
    return view


async def _delegated_token_status(provider: str, user_id: UUID) -> dict[str, Any]:
    """Return delegated token status, refreshing providers that support it."""
    if provider == "azure":
        try:
            from app.services.connector_commands import get_fresh_azure_token

            token_data = await get_fresh_azure_token(user_id)
            return token_status_from_data(provider, token_data)
        except Exception as exc:
            logger.warning("Azure token refresh status check failed for user %s: %s", user_id.hex[:12], exc)
    return await token_status(provider, user_id)


async def upsert_delegated_account(
    db: AsyncSession,
    provider: str,
    user_id: UUID,
    *,
    token_data: Optional[dict[str, Any]] = None,
    status: str = "connected",
    username: Optional[str] = None,
    permission_summary: Optional[str] = None,
    commit: bool = False,
) -> AIConnectedAccount:
    now = _utcnow()
    account = await get_account(db, user_id, provider)
    if not account:
        account = AIConnectedAccount(
            user_id=user_id,
            provider=provider,
            target_environment="production",
            created_at=now,
        )
        db.add(account)

    account.status = status
    account.provider_username = username or _token_username(token_data or {}, account.provider_username or f"{provider}-user")
    account.scopes = (token_data or {}).get("scope") or account.scopes
    account.secret_reference = token_secret_name(provider, user_id)
    account.permission_summary = permission_summary if permission_summary is not None else account.permission_summary
    account.last_verified_at = now if status == "connected" else account.last_verified_at
    account.disconnected_at = None if status == "connected" else account.disconnected_at
    account.updated_at = now

    if commit:
        await db.commit()
        await db.refresh(account)
    return account


async def mark_delegated_account_disconnected(
    db: AsyncSession,
    provider: str,
    user_id: UUID,
    *,
    commit: bool = False,
) -> Optional[AIConnectedAccount]:
    account = await get_account(db, user_id, provider)
    if not account:
        return None
    now = _utcnow()
    account.status = "disconnected"
    account.secret_reference = None
    account.permission_summary = None
    account.disconnected_at = now
    account.updated_at = now
    if commit:
        await db.commit()
        await db.refresh(account)
    return account


async def sync_delegated_account_from_token(
    db: AsyncSession,
    provider: str,
    user_id: UUID,
    *,
    commit: bool = False,
) -> dict[str, Any]:
    token_state = await _delegated_token_status(provider, user_id)
    account = await get_account(db, user_id, provider)
    effective_status = effective_delegated_status(account, token_state)

    if token_state.get("status") == "connected" and effective_status == "connected":
        account = await upsert_delegated_account(
            db,
            provider,
            user_id,
            token_data=token_state,
            status="connected",
            commit=commit,
        )
    elif token_state.get("status") == "expired" and account:
        account.status = "expired"
        account.updated_at = _utcnow()
        if commit:
            await db.commit()
            await db.refresh(account)
    elif token_state.get("status") == "not_connected":
        await mark_delegated_account_disconnected(db, provider, user_id, commit=commit)

    return {
        **token_state,
        "status": effective_status,
        "provider": provider,
    }


async def record_delegated_diagnosis(
    db: AsyncSession,
    provider: str,
    user_id: UUID,
    diagnosis: dict[str, Any],
    *,
    commit: bool = False,
) -> None:
    token_state = await _delegated_token_status(provider, user_id)
    message = diagnosis.get("message") or diagnosis.get("error") or ""
    if diagnosis.get("status") == "success":
        await upsert_delegated_account(
            db,
            provider,
            user_id,
            token_data=token_state,
            status="connected",
            username=diagnosis.get("login") or token_state.get("username"),
            permission_summary=message,
            commit=commit,
        )
        return

    if token_state.get("status") == "not_connected":
        await mark_delegated_account_disconnected(db, provider, user_id, commit=commit)
        return

    if token_state.get("status") == "expired":
        account = await get_account(db, user_id, provider)
        if account:
            account.status = "expired"
            account.permission_summary = message
            account.updated_at = _utcnow()
            if commit:
                await db.commit()
                await db.refresh(account)
        return

    await upsert_delegated_account(
        db,
        provider,
        user_id,
        token_data=token_state,
        status="error",
        username=diagnosis.get("login") or token_state.get("username"),
        permission_summary=message,
        commit=commit,
    )


async def effective_connected_accounts(
    db: AsyncSession,
    user_id: Optional[UUID],
    *,
    include_token_state: bool = False,
) -> list[AIConnectedAccount]:
    """Return connected-account state for normal request paths.

    The database row is the fast source of truth for page rendering, chat
    routing, and tool selection. Token-store reconciliation is intentionally
    opt-in because Key Vault checks can be slow and should only run on explicit
    connect/status/diagnose/disconnect paths.
    """
    if not user_id:
        return []

    result = await db.execute(select(AIConnectedAccount).where(AIConnectedAccount.user_id == user_id))
    accounts = list(result.scalars().all())
    if not include_token_state:
        return accounts

    by_provider = {account.provider: account for account in accounts}

    for provider in DELEGATED_TOKEN_PROVIDERS:
        token_state = await _delegated_token_status(provider, user_id)
        effective_status = effective_delegated_status(by_provider.get(provider), token_state)
        account = by_provider.get(provider)
        if account:
            accounts[accounts.index(account)] = _delegated_account_view(account, provider, user_id, token_state, effective_status)
            continue
        if token_state.get("status") in {"connected", "expired"}:
            accounts.append(_delegated_account_view(None, provider, user_id, token_state, effective_status))

    return accounts
