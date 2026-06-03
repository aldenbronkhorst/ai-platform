"""Shared Key Vault client helpers."""
import asyncio
import os
from functools import lru_cache
from typing import Optional

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


def key_vault_uri() -> str:
    return os.environ.get("KEY_VAULT_URI", "")


@lru_cache(maxsize=4)
def _cached_secret_client(vault_url: str) -> SecretClient:
    return SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())


def get_secret_client(vault_url: Optional[str] = None) -> Optional[SecretClient]:
    uri = vault_url or key_vault_uri()
    return _cached_secret_client(uri) if uri else None


async def get_secret_value(secret_name: str, vault_url: Optional[str] = None) -> str:
    client = get_secret_client(vault_url)
    if not client:
        return ""
    secret = await asyncio.to_thread(client.get_secret, secret_name)
    return secret.value or ""


async def set_secret_value(secret_name: str, secret_value: str, vault_url: Optional[str] = None) -> None:
    client = get_secret_client(vault_url)
    if not client:
        raise RuntimeError("KEY_VAULT_URI is not configured.")
    await asyncio.to_thread(client.set_secret, secret_name, secret_value)


async def delete_secret(secret_name: str, vault_url: Optional[str] = None) -> None:
    client = get_secret_client(vault_url)
    if not client:
        return

    def _delete() -> None:
        poller = client.begin_delete_secret(secret_name)
        poller.wait()

    await asyncio.to_thread(_delete)
