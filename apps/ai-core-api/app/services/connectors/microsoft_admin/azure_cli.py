"""Azure CLI profile and execution for the Microsoft Admin connector."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import time
import uuid
from typing import Any, Optional
from uuid import UUID

import httpx

from app.services.ops_command_runner import run_command
from app.services.connectors.microsoft_admin.constants import (
    AZURE_ARM_SCOPE,
    AZURE_CLI_ARM_RESOURCE,
    AZURE_CLI_ARM_TARGET,
    AZURE_CLI_CLIENT_ID,
    AZURE_ENVIRONMENT_NAME,
    AZURE_TOKEN_ENDPOINT,
    MICROSOFT_ADMIN_CLIENT_ID,
    MS_AZURE_CLI_ALLOWED_BINARIES,
    TENANT_ID,
    microsoft_admin_arm_device_scope_string,
)
from app.services.connectors.microsoft_admin.powershell_common import (
    _command_failure_message,
    _failed_microsoft_admin_result,
    _tool_timeout,
)
from app.services.connectors.microsoft_admin.tokens import (
    _decode_jwt_claims,
    _expires_on,
    _get_fresh_microsoft_admin_token_for_scope,
    _microsoft_identity_claims,
    _token_expired,
    extract_microsoft_admin_username,
)

logger = logging.getLogger(__name__)

def _normalize_azure_command(command: str) -> str:
    command = command.strip()
    return command if command.startswith("az ") else f"az {command}"

async def _run_microsoft_admin_azure_cli(
    command: str,
    user_id: Optional[UUID],
    timeout: int,
    request_id: str,
    *,
    connector_name: str = "microsoft_admin",
    allowed_binaries: set[str] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_azure_command(command)

    token_data = await _get_fresh_microsoft_admin_token_for_scope(
        user_id,
        AZURE_ARM_SCOPE,
        require_account_metadata=True,
    ) if user_id else None
    if not token_data or not token_data.get("access_token"):
        message = (
            token_data.get("refresh_error")
            if isinstance(token_data, dict) and token_data.get("refresh_error")
            else "Azure Resource Manager access is not connected for this Microsoft Admin user."
        )
        error_type = (
            token_data.get("error_type")
            if isinstance(token_data, dict) and token_data.get("error_type")
            else "not_connected"
        )
        result = _failed_microsoft_admin_result(
            request_id=request_id,
            mode="ms_azure_cli",
            message=message,
            command=normalized,
            error_type=error_type,
            connector=connector_name,
        )
        result["auth_method"] = error_type
        return result
    if _token_expired(token_data):
        result = _failed_microsoft_admin_result(
            request_id=request_id,
            mode="ms_azure_cli",
            message="Azure Resource Manager token is expired. Reconnect Microsoft Admin for this user.",
            command=normalized,
            error_type="expired_user_token",
            connector=connector_name,
        )
        result["auth_method"] = "expired_user_token"
        return result

    profile = await ensure_azure_cli_profile(user_id, token_data)
    if not profile.get("ready"):
        result = _failed_microsoft_admin_result(
            request_id=request_id,
            mode="ms_azure_cli",
            message=profile.get("message", "Azure Resource Manager CLI profile could not be prepared for this Microsoft Admin user."),
            command=normalized,
            error_type="profile_not_ready",
            connector=connector_name,
        )
        result["auth_method"] = "user_scoped_microsoft_admin_shell"
        return result

    env: dict[str, str] = {
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_CONFIG_DIR": profile.get("config_dir") or _azure_config_dir(user_id),
    }

    result = await run_command(
        normalized,
        timeout=timeout,
        env=env,
        allowed_binaries=allowed_binaries or MS_AZURE_CLI_ALLOWED_BINARIES,
    )
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": connector_name,
        "mode": "ms_azure_cli",
        "subtool": "ms_azure_cli",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "user_scoped_microsoft_admin_shell",
    })
    if not result.success:
        output.setdefault("error_type", "command_failed")
        output.setdefault("message", _command_failure_message(output, "Azure Resource Manager CLI command failed."))
    return output

async def run_ms_azure_cli_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute the Azure Resource Manager CLI surface for the Microsoft Admin connector."""
    request_id = uuid.uuid4().hex[:16]
    timeout = _tool_timeout(arguments, timeout)
    command = str(arguments.get("command") or "").strip()
    if not command:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="ms_azure_cli",
            message="Provide command for ms_azure_cli.",
            connector="ms_azure_cli",
        )
    return await _run_microsoft_admin_azure_cli(
        command,
        user_id,
        timeout=timeout,
        request_id=request_id,
        connector_name="ms_azure_cli",
        allowed_binaries=MS_AZURE_CLI_ALLOWED_BINARIES,
    )

def _azure_config_dir(user_id: UUID) -> str:
    base = os.environ.get("AZURE_CLI_USER_CONFIG_ROOT", "/tmp/ai-platform-azure-cli")
    path = os.path.join(base, user_id.hex)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path

async def ensure_azure_cli_profile(
    user_id: UUID,
    token_data: dict[str, Any],
    subscriptions_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Persist an isolated Azure Resource Manager CLI profile/cache for the connected user."""
    if not token_data.get("access_token"):
        return {"ready": False, "message": "Azure Resource Manager token is not available for this Microsoft Admin connection."}

    username = extract_microsoft_admin_username(token_data)
    if not username:
        return {
            "ready": False,
            "message": (
                "Microsoft Admin sign-in returned an Azure Resource Manager token but no usable user identity. "
                "Reconnect Microsoft Admin so the platform can store a user-scoped CLI session."
            ),
        }

    subscriptions_result = subscriptions_result or await _list_azure_subscriptions(token_data["access_token"])
    if not subscriptions_result.get("ok"):
        return {
            "ready": False,
            "message": subscriptions_result.get("message", "Azure subscription discovery failed."),
        }

    config_dir = _azure_config_dir(user_id)
    await asyncio.to_thread(
        _write_azure_cli_files,
        config_dir,
        token_data,
        username,
        subscriptions_result.get("subscriptions", []),
    )
    return {
        "ready": True,
        "username": username,
        "subscriptions": len(subscriptions_result.get("subscriptions", [])),
        "config_dir": config_dir,
    }


async def _list_azure_subscriptions(access_token: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://management.azure.com/subscriptions?api-version=2020-01-01",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code >= 400:
            return {
                "ok": False,
                "message": f"Azure subscription discovery failed with HTTP {response.status_code}.",
                "stderr": response.text[:1000],
            }
        return {"ok": True, "subscriptions": response.json().get("value", [])}
    except Exception as exc:
        logger.warning("Azure subscription discovery failed: %s", exc)
        return {"ok": False, "message": "Azure subscription discovery failed. Check connector logs."}


def _write_azure_cli_files(config_dir: str, token_data: dict[str, Any], username: str, subscriptions: list[dict[str, Any]]) -> None:
    path = Path(config_dir)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    _write_azure_cli_token_cache(path, token_data)
    default_subscription_id = _write_azure_profile(path, username, subscriptions)
    _write_azure_cloud_config(path, default_subscription_id)
    _write_azure_config(path)


def _write_azure_cli_token_cache(config_dir: Path, token_data: dict[str, Any]) -> None:
    import msal

    cache_path = config_dir / "msal_token_cache.json"
    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        try:
            cache.deserialize(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = msal.SerializableTokenCache()

    client_info = _azure_client_info(token_data)
    _add_msal_cache_token(
        cache,
        token_data,
        client_info=client_info,
        client_id=token_data.get("client_id") or MICROSOFT_ADMIN_CLIENT_ID,
        scope=token_data.get("scope") or microsoft_admin_arm_device_scope_string(),
        include_refresh_token=True,
    )
    # Azure CLI looks up ARM access tokens by its own first-party client id and
    # legacy ARM resource target, even when the stored token came from our app.
    _add_msal_cache_token(
        cache,
        token_data,
        client_info=client_info,
        client_id=AZURE_CLI_CLIENT_ID,
        scope=AZURE_CLI_ARM_TARGET,
        include_refresh_token=False,
    )
    _atomic_write(cache_path, cache.serialize(), mode=0o600)


def _add_msal_cache_token(
    cache: Any,
    token_data: dict[str, Any],
    *,
    client_info: str,
    client_id: str,
    scope: str,
    include_refresh_token: bool,
) -> None:
    response = {
        "token_type": token_data.get("token_type") or "Bearer",
        "access_token": token_data.get("access_token"),
        "id_token": token_data.get("id_token"),
        "id_token_claims": _microsoft_identity_claims(token_data),
        "client_info": client_info,
        "scope": scope,
        "expires_in": int(token_data.get("expires_in") or max(_expires_on(token_data) - int(time.time()), 0) or 3600),
    }
    if include_refresh_token:
        response["refresh_token"] = token_data.get("refresh_token")
    response = {key: value for key, value in response.items() if value}
    event = {
        "client_id": client_id,
        "scope": scope.split(),
        "token_endpoint": AZURE_TOKEN_ENDPOINT,
        "environment": "login.microsoftonline.com",
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "response": response,
        "data": {"username": token_data.get("username") or extract_microsoft_admin_username(token_data)},
    }
    cache.add(event)


def _has_azure_cli_account_metadata(token_data: dict[str, Any]) -> bool:
    return bool(_azure_client_info(token_data))


def _azure_client_info(token_data: dict[str, Any]) -> str:
    existing = str(token_data.get("client_info") or "").strip()
    if existing:
        return existing

    claims = _microsoft_identity_claims(token_data) or _decode_jwt_claims(str(token_data.get("access_token") or ""))
    uid = claims.get("oid") or claims.get("sub")
    utid = claims.get("tid") or claims.get("tenant_id") or TENANT_ID
    if not uid or not utid:
        return ""

    payload = json.dumps({"uid": uid, "utid": utid}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _write_azure_profile(config_dir: Path, username: str, subscriptions: list[dict[str, Any]]) -> str:
    profile_path = config_dir / "azureProfile.json"
    profile_subscriptions: list[dict[str, Any]] = []
    for index, subscription in enumerate(subscriptions):
        subscription_id = subscription.get("subscriptionId") or subscription.get("id")
        if not subscription_id:
            continue
        profile_subscriptions.append({
            "id": subscription_id,
            "name": subscription.get("displayName") or subscription.get("name") or subscription_id,
            "state": subscription.get("state") or "Enabled",
            "user": {"name": username, "type": "user"},
            "isDefault": index == 0,
            "tenantId": subscription.get("tenantId") or subscription.get("homeTenantId") or TENANT_ID,
            "environmentName": AZURE_ENVIRONMENT_NAME,
            "homeTenantId": subscription.get("homeTenantId") or subscription.get("tenantId") or TENANT_ID,
            "managedByTenants": subscription.get("managedByTenants") or [],
        })

    if not profile_subscriptions:
        profile_subscriptions.append({
            "id": TENANT_ID,
            "name": "N/A(tenant level account)",
            "state": "Enabled",
            "user": {"name": username, "type": "user"},
            "isDefault": True,
            "tenantId": TENANT_ID,
            "environmentName": AZURE_ENVIRONMENT_NAME,
        })

    profile = {
        "installationId": str(uuid.uuid4()),
        "subscriptions": profile_subscriptions,
    }
    _atomic_write(profile_path, json.dumps(profile, indent=2), mode=0o600)
    return profile_subscriptions[0]["id"]


def _write_azure_cloud_config(config_dir: Path, subscription_id: str) -> None:
    content = f"[{AZURE_ENVIRONMENT_NAME}]\nsubscription = {subscription_id}\n\n"
    _atomic_write(config_dir / "clouds.config", content, mode=0o600)


def _write_azure_config(config_dir: Path) -> None:
    content = f"[cloud]\nname = {AZURE_ENVIRONMENT_NAME}\n"
    _atomic_write(config_dir / "config", content, mode=0o600)


def _atomic_write(path: Path, content: str, mode: int) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.chmod(tmp_path, mode)
    tmp_path.replace(path)

async def validate_azure_cli_profile(user_id: UUID, timeout: int = 20) -> dict[str, Any]:
    env = {
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_CONFIG_DIR": _azure_config_dir(user_id),
    }
    result = await run_command(
        f"az account get-access-token --resource {AZURE_CLI_ARM_RESOURCE} --only-show-errors -o json",
        timeout=timeout,
        env=env,
        allowed_binaries=MS_AZURE_CLI_ALLOWED_BINARIES,
    )
    if result.success:
        return {"ready": True, "stdout": result.stdout}
    output = result.to_dict()
    return {
        "ready": False,
        "message": output.get("error") or output.get("stderr") or "Azure Resource Manager CLI profile validation failed.",
        "stderr": output.get("stderr", ""),
        "exit_code": output.get("exit_code"),
    }
