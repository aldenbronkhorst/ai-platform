"""User-scoped command helpers for native Azure and GitHub connectors."""
import os
import uuid
from typing import Any, Optional
from uuid import UUID

import httpx

from app.services.ops_command_runner import run_command
from app.services.token_storage import retrieve_token


TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")


def _normalize_azure_command(command: str) -> str:
    command = command.strip()
    return command if command.startswith("az ") else f"az {command}"


async def run_azure_cli_command(command: str, user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    token_data = await retrieve_token("azure", user_id) if user_id else None
    env: dict[str, str] = {}
    if token_data and token_data.get("access_token"):
        env.update({
            "AZURE_ACCESS_TOKEN": token_data["access_token"],
            "AZURE_TENANT_ID": TENANT_ID,
        })

    normalized = _normalize_azure_command(command)
    request_id = uuid.uuid4().hex[:16]
    result = await run_command(normalized, timeout=timeout, env=env)
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": "azure_cli",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "oauth_token_env" if token_data else "managed_identity",
    })
    return output


async def diagnose_azure_connection(user_id: Optional[UUID]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await retrieve_token("azure", user_id) if user_id else None
    access_token = token_data.get("access_token") if token_data else None
    if not access_token:
        return {
            "status": "failed",
            "connector": "azure_cli",
            "request_id": request_id,
            "message": "Azure is not connected for this user.",
        }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://management.azure.com/subscriptions?api-version=2020-01-01",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code >= 400:
            return {
                "status": "failed",
                "connector": "azure_cli",
                "request_id": request_id,
                "message": f"Azure token check failed with HTTP {response.status_code}.",
                "stderr": response.text[:1000],
            }
        subscriptions = response.json().get("value", [])
        return {
            "status": "success",
            "connector": "azure_cli",
            "request_id": request_id,
            "message": f"Azure token is valid. Visible subscriptions: {len(subscriptions)}.",
            "subscriptions": [
                {
                    "subscription_id": sub.get("subscriptionId"),
                    "display_name": sub.get("displayName"),
                    "state": sub.get("state"),
                }
                for sub in subscriptions[:10]
            ],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "connector": "azure_cli",
            "request_id": request_id,
            "message": f"Azure diagnostics failed: {exc}",
        }


async def run_github_cli_command(command: str, user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    token_data = await retrieve_token("github", user_id) if user_id else None
    access_token = token_data.get("access_token") if token_data else None
    env: dict[str, str] = {}
    if access_token:
        env.update({"GH_TOKEN": access_token, "GITHUB_TOKEN": access_token})

    request_id = uuid.uuid4().hex[:16]
    result = await run_command(command.strip(), timeout=timeout, env=env)
    output = result.to_dict()
    output.update({
        "command": command.strip(),
        "connector": "github_cli",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "oauth_token_env" if access_token else "none",
    })
    return output
