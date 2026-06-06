"""Per-user token storage in Key Vault for delegated auth flows."""
import json
import logging
import time
from typing import Any, Optional
from uuid import UUID

from app.services.key_vault import (
    delete_secret,
    get_secret_value,
    key_vault_uri,
    recover_deleted_secret,
    set_secret_value,
)

logger = logging.getLogger(__name__)


def _secret_name(provider: str, user_id: UUID) -> str:
    return f"connector-token-{provider}-{user_id.hex[:12]}"


def token_secret_name(provider: str, user_id: UUID) -> str:
    """Return the Key Vault secret name used for a user's delegated connector token."""
    return _secret_name(provider, user_id)


def _is_recoverable_deleted_secret_error(exc: Exception) -> bool:
    text = str(exc).lower()
    error_code = str(getattr(exc, "error_code", "") or "").lower()
    return (
        "deleted but recoverable" in text
        or "objectisdeletedbutrecoverable" in text
        or "deletedsecretrecoverable" in text
        or "deletedsecretrecoverable" in error_code
    )


async def store_token(provider: str, user_id: UUID, token_data: dict[str, Any]) -> bool:
    """Store OAuth token data in Key Vault for a specific user and provider."""
    if not key_vault_uri():
        logger.warning("KEY_VAULT_URI not set, cannot store token")
        return False
    secret_name = _secret_name(provider, user_id)
    secret_value = json.dumps(token_data)
    try:
        await set_secret_value(secret_name, secret_value)
        logger.info("Stored %s token for user %s", provider, user_id.hex[:12])
        return True
    except Exception as e:
        if _is_recoverable_deleted_secret_error(e):
            try:
                await recover_deleted_secret(secret_name)
                await set_secret_value(secret_name, secret_value)
                logger.info("Recovered and stored %s token for user %s", provider, user_id.hex[:12])
                return True
            except Exception as recover_error:
                logger.error(
                    "Failed to recover and store %s token for user %s: %s",
                    provider,
                    user_id.hex[:12],
                    recover_error,
                )
                return False
        logger.error("Failed to store %s token for user %s: %s", provider, user_id.hex[:12], e)
        return False


async def retrieve_token(provider: str, user_id: UUID) -> Optional[dict[str, Any]]:
    """Retrieve OAuth token data from Key Vault."""
    if not key_vault_uri():
        return None
    try:
        secret_value = await get_secret_value(_secret_name(provider, user_id))
        return json.loads(secret_value)
    except Exception as e:
        logger.warning("No %s token found for user %s: %s", provider, user_id.hex[:12], e)
        return None


async def delete_token(provider: str, user_id: UUID) -> bool:
    """Delete OAuth token from Key Vault (disconnect)."""
    if not key_vault_uri():
        return False
    try:
        await delete_secret(_secret_name(provider, user_id))
        logger.info("Deleted %s token for user %s", provider, user_id.hex[:12])
        return True
    except Exception as e:
        logger.warning("Failed to delete %s token for user %s: %s", provider, user_id.hex[:12], e)
        return False


def token_status_from_data(provider: str, token: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Return connection status metadata for an already-loaded token payload."""
    if not token:
        return {"status": "not_connected", "provider": provider}
    expires_on = token.get("expires_on")
    try:
        expires_ts = int(expires_on) if expires_on else None
    except (TypeError, ValueError):
        expires_ts = None

    status = "expired" if expires_ts and expires_ts <= int(time.time()) else "connected"
    return {
        "status": status,
        "provider": provider,
        "token_type": token.get("token_type", "unknown"),
        "expires_on": token.get("expires_on"),
        "scope": token.get("scope", ""),
        "username": token.get("username") or token.get("login") or token.get("provider_username"),
        "login": token.get("login"),
    }


async def token_status(provider: str, user_id: UUID) -> dict[str, Any]:
    """Check if a token exists and its expiry status."""
    return token_status_from_data(provider, await retrieve_token(provider, user_id))
