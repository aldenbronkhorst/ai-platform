"""Per-user token storage in Key Vault for delegated auth flows."""
import asyncio
import json
import logging
import os
from typing import Any, Optional
from uuid import UUID
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

logger = logging.getLogger(__name__)

KV_URI = os.environ.get("KEY_VAULT_URI", "")


def _secret_name(provider: str, user_id: UUID) -> str:
    return f"connector-token-{provider}-{user_id.hex[:12]}"


async def store_token(provider: str, user_id: UUID, token_data: dict[str, Any]) -> bool:
    """Store OAuth token data in Key Vault for a specific user and provider."""
    if not KV_URI:
        logger.warning("KEY_VAULT_URI not set, cannot store token")
        return False
    try:
        def _store() -> None:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=KV_URI, credential=credential)
            client.set_secret(_secret_name(provider, user_id), json.dumps(token_data))

        await asyncio.to_thread(_store)
        logger.info("Stored %s token for user %s", provider, user_id.hex[:12])
        return True
    except Exception as e:
        logger.error("Failed to store %s token for user %s: %s", provider, user_id.hex[:12], e)
        return False


async def retrieve_token(provider: str, user_id: UUID) -> Optional[dict[str, Any]]:
    """Retrieve OAuth token data from Key Vault."""
    if not KV_URI:
        return None
    try:
        def _retrieve() -> str:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=KV_URI, credential=credential)
            secret = client.get_secret(_secret_name(provider, user_id))
            return secret.value or "{}"

        secret_value = await asyncio.to_thread(_retrieve)
        return json.loads(secret_value)
    except Exception as e:
        logger.warning("No %s token found for user %s: %s", provider, user_id.hex[:12], e)
        return None


async def delete_token(provider: str, user_id: UUID) -> bool:
    """Delete OAuth token from Key Vault (disconnect)."""
    if not KV_URI:
        return False
    try:
        def _delete() -> None:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=KV_URI, credential=credential)
            poller = client.begin_delete_secret(_secret_name(provider, user_id))
            poller.wait()

        await asyncio.to_thread(_delete)
        logger.info("Deleted %s token for user %s", provider, user_id.hex[:12])
        return True
    except Exception as e:
        logger.warning("Failed to delete %s token for user %s: %s", provider, user_id.hex[:12], e)
        return False


async def token_status(provider: str, user_id: UUID) -> dict[str, Any]:
    """Check if a token exists and its expiry status."""
    token = await retrieve_token(provider, user_id)
    if not token:
        return {"status": "not_connected", "provider": provider}
    return {
        "status": "connected",
        "provider": provider,
        "token_type": token.get("token_type", "unknown"),
        "expires_on": token.get("expires_on"),
        "scope": token.get("scope", ""),
    }
