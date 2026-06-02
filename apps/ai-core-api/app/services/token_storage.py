"""Per-user token storage in Key Vault for delegated auth flows."""
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
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KV_URI, credential=credential)
        name = _secret_name(provider, user_id)
        client.set_secret(name, json.dumps(token_data))
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
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KV_URI, credential=credential)
        name = _secret_name(provider, user_id)
        secret = client.get_secret(name)
        return json.loads(secret.value)
    except Exception as e:
        logger.warning("No %s token found for user %s: %s", provider, user_id.hex[:12], e)
        return None


async def delete_token(provider: str, user_id: UUID) -> bool:
    """Delete OAuth token from Key Vault (disconnect)."""
    if not KV_URI:
        return False
    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KV_URI, credential=credential)
        name = _secret_name(provider, user_id)
        poller = client.begin_delete_secret(name)
        poller.wait()
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
