"""Per-user token storage in Key Vault for delegated auth flows."""
import json
import logging
import re
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

KEY_VAULT_SECRET_VALUE_SOFT_LIMIT = 25_000
MICROSOFT_NATIVE_TOKEN_PROVIDERS = {
    "azure_cli",
    "microsoft_graph",
    "exchange_online",
    "teams_admin",
    "sharepoint_pnp",
}
MICROSOFT_TOKEN_STORAGE_PROVIDERS = {*MICROSOFT_NATIVE_TOKEN_PROVIDERS, "microsoft_admin"}

MICROSOFT_ADMIN_TOKEN_TOP_LEVEL_KEYS = {
    "provider",
    "client_id",
    "token_type",
    "access_token",
    "refresh_token",
    "scope",
    "scope_profile",
    "client_info",
    "username",
    "provider_username",
    "login",
    "expires_in",
    "expires_on",
    "consented_scope_profiles",
    "refresh_error",
    "error_type",
}

MICROSOFT_ADMIN_DELEGATED_TOKEN_KEYS = {
    "client_id",
    "token_type",
    "access_token",
    "scope",
    "scope_profile",
    "client_info",
    "expires_in",
    "expires_on",
    "refresh_error",
    "error_type",
}


def _secret_name(provider: str, user_id: UUID) -> str:
    provider_segment = re.sub(r"[^0-9A-Za-z-]+", "-", provider).strip("-").lower()
    if not provider_segment:
        provider_segment = "connector"
    return f"connector-token-{provider_segment}-{user_id.hex[:12]}"


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


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != {}


def _microsoft_admin_delegated_tokens_for_storage(
    token_data: dict[str, Any],
    compact_token_data: dict[str, Any],
    *,
    include_delegated_access_tokens: bool,
) -> dict[str, dict[str, Any]]:
    delegated_tokens = token_data.get("delegated_tokens") or {}
    if not isinstance(delegated_tokens, dict):
        return {}

    primary_profile = compact_token_data.get("scope_profile")
    compact_delegated_tokens: dict[str, dict[str, Any]] = {}
    for profile, profile_token in delegated_tokens.items():
        if not isinstance(profile_token, dict):
            continue
        if profile == primary_profile:
            continue

        compact_profile = {
            key: profile_token.get(key)
            for key in MICROSOFT_ADMIN_DELEGATED_TOKEN_KEYS
            if _has_value(profile_token.get(key))
        }
        compact_profile.setdefault("scope_profile", profile)
        if not include_delegated_access_tokens:
            compact_profile.pop("access_token", None)

        if compact_token_data.get("refresh_token") is None and _has_value(profile_token.get("refresh_token")):
            compact_profile["refresh_token"] = profile_token["refresh_token"]

        if compact_profile.get("access_token") or compact_profile.get("refresh_token") or compact_profile.get("scope_profile"):
            compact_delegated_tokens[str(profile)] = compact_profile

    return compact_delegated_tokens


def _compact_microsoft_admin_token_for_storage(
    token_data: dict[str, Any],
    *,
    include_delegated_access_tokens: bool = True,
) -> dict[str, Any]:
    """Drop nonessential Microsoft identity blobs before storing in Key Vault.

    Microsoft native connector secrets only need access/refresh token material
    plus account metadata. ID tokens, decoded claims, and duplicate primary
    delegated tokens push secrets toward Key Vault's 25,600 character value
    limit while not being needed after we have stored the username.
    client_info is deliberately kept because Azure CLI/MSAL needs it to create a
    user account entry in its token cache.
    """
    compact_token_data = {
        key: token_data.get(key)
        for key in MICROSOFT_ADMIN_TOKEN_TOP_LEVEL_KEYS
        if _has_value(token_data.get(key))
    }
    delegated_tokens = _microsoft_admin_delegated_tokens_for_storage(
        token_data,
        compact_token_data,
        include_delegated_access_tokens=include_delegated_access_tokens,
    )
    if delegated_tokens:
        compact_token_data["delegated_tokens"] = delegated_tokens
    return compact_token_data


def _token_for_storage(provider: str, token_data: dict[str, Any]) -> dict[str, Any]:
    if provider not in MICROSOFT_TOKEN_STORAGE_PROVIDERS:
        return token_data

    compact_token_data = _compact_microsoft_admin_token_for_storage({**token_data, "provider": provider})
    secret_value = json.dumps(compact_token_data, separators=(",", ":"))
    if len(secret_value) <= KEY_VAULT_SECRET_VALUE_SOFT_LIMIT:
        return compact_token_data

    compact_token_data = _compact_microsoft_admin_token_for_storage(
        {**token_data, "provider": provider},
        include_delegated_access_tokens=False,
    )
    secret_value = json.dumps(compact_token_data, separators=(",", ":"))
    if len(secret_value) > KEY_VAULT_SECRET_VALUE_SOFT_LIMIT:
        logger.warning(
            "Compacted legacy Microsoft token payload is still large (%s characters)",
            len(secret_value),
        )
    return compact_token_data


async def store_token(provider: str, user_id: UUID, token_data: dict[str, Any]) -> bool:
    """Store OAuth token data in Key Vault for a specific user and provider."""
    if not key_vault_uri():
        logger.warning("KEY_VAULT_URI not set, cannot store token")
        return False
    secret_name = _secret_name(provider, user_id)
    secret_value = json.dumps(_token_for_storage(provider, token_data), separators=(",", ":"))
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
    if token.get("refresh_error") and not token.get("access_token"):
        return {
            "status": "error",
            "provider": provider,
            "token_type": token.get("token_type", "unknown"),
            "expires_on": token.get("expires_on"),
            "scope": token.get("scope", ""),
            "username": token.get("username") or token.get("login") or token.get("provider_username"),
            "login": token.get("login"),
            "error": token.get("refresh_error"),
            "error_type": token.get("error_type"),
        }
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
