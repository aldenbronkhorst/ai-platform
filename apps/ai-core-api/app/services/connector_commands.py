"""User-scoped command helpers for native Azure and GitHub connectors."""
import asyncio
import base64
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Optional
from uuid import UUID

import httpx

from app.services.ops_command_runner import run_command
from app.services.token_storage import retrieve_token, store_token


TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")
AZURE_CLI_CLIENT_ID = os.environ.get("AZURE_CLI_CLIENT_ID", "04b07795-8ddb-461a-bbee-02f9e1bf7b46")
AZURE_AUTHORITY_HOST = os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com")
AZURE_TOKEN_ENDPOINT = f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/v2.0/token"
AZURE_ARM_SCOPE = os.environ.get("AZURE_ARM_SCOPE", "https://management.core.windows.net//.default")
AZURE_DEVICE_SCOPES = [AZURE_ARM_SCOPE, "openid", "profile", "offline_access"]
AZURE_ENVIRONMENT_NAME = os.environ.get("AZURE_ENVIRONMENT_NAME", "AzureCloud")


def _normalize_azure_command(command: str) -> str:
    command = command.strip()
    return command if command.startswith("az ") else f"az {command}"


def azure_device_scope_string() -> str:
    return " ".join(AZURE_DEVICE_SCOPES)


def azure_token_request_data() -> dict[str, str]:
    """Return token request fields that mirror Azure CLI/MSAL device auth."""
    return {"scope": azure_device_scope_string(), "client_info": "1"}


async def run_azure_cli_command(command: str, user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await _get_fresh_azure_token(user_id) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "timed_out": False,
            "output_truncated": False,
            "stdout_chars": 0,
            "stderr_chars": 0,
            "error": "Azure is not connected for this user.",
            "command": _normalize_azure_command(command),
            "connector": "azure_cli",
            "request_id": request_id,
            "status": "failed",
            "auth_method": "not_connected",
        }
    if _token_expired(token_data):
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "timed_out": False,
            "output_truncated": False,
            "stdout_chars": 0,
            "stderr_chars": 0,
            "error": "Azure token is expired. Reconnect Azure for this user.",
            "command": _normalize_azure_command(command),
            "connector": "azure_cli",
            "request_id": request_id,
            "status": "failed",
            "auth_method": "expired_user_token",
        }

    profile = await ensure_azure_cli_profile(user_id, token_data)
    if not profile.get("ready"):
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "timed_out": False,
            "output_truncated": False,
            "stdout_chars": 0,
            "stderr_chars": 0,
            "error": profile.get("message", "Azure CLI profile could not be prepared for this user."),
            "command": _normalize_azure_command(command),
            "connector": "azure_cli",
            "request_id": request_id,
            "status": "failed",
            "auth_method": "user_scoped_azure_cli",
        }

    env: dict[str, str] = {
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_CONFIG_DIR": _azure_config_dir(user_id),
    }

    normalized = _normalize_azure_command(command)
    result = await run_command(normalized, timeout=timeout, env=env)
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": "azure_cli",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "user_scoped_azure_cli",
    })
    return output


def _azure_config_dir(user_id: UUID) -> str:
    base = os.environ.get("AZURE_CLI_USER_CONFIG_ROOT", "/tmp/ai-platform-azure-cli")
    path = os.path.join(base, user_id.hex)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


async def _get_fresh_azure_token(user_id: Optional[UUID]) -> Optional[dict[str, Any]]:
    if not user_id:
        return None
    token_data = await retrieve_token("azure", user_id)
    if not token_data:
        return None
    expires_on = _expires_on(token_data)
    if token_data.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
        return token_data
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return token_data
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                AZURE_TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": token_data.get("client_id") or AZURE_CLI_CLIENT_ID,
                    "refresh_token": refresh_token,
                    **azure_token_request_data(),
                },
            )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            return {**token_data, "refresh_error": data.get("error_description") or data.get("error") or response.text[:500]}
        updated = {
            **token_data,
            "client_id": token_data.get("client_id") or AZURE_CLI_CLIENT_ID,
            "token_type": data.get("token_type", token_data.get("token_type")),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token", refresh_token),
            "scope": data.get("scope", token_data.get("scope")),
            "id_token": data.get("id_token", token_data.get("id_token")),
            "id_token_claims": _azure_identity_claims({"id_token": data.get("id_token")}) or token_data.get("id_token_claims"),
            "client_info": data.get("client_info", token_data.get("client_info")),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        updated["username"] = extract_azure_username(updated)
        await store_token("azure", user_id, updated)
        await ensure_azure_cli_profile(user_id, updated)
        return updated
    except Exception as exc:
        return {**token_data, "refresh_error": str(exc)}


def _expires_on(token_data: dict[str, Any]) -> int:
    try:
        return int(token_data.get("expires_on") or 0)
    except (TypeError, ValueError):
        return 0


def _token_expired(token_data: dict[str, Any]) -> bool:
    expires_on = _expires_on(token_data)
    return bool(expires_on and expires_on <= int(time.time()))


def extract_azure_username(token_data: dict[str, Any]) -> str:
    for claims in _azure_claim_sets(token_data):
        for key in ("preferred_username", "email", "upn", "unique_name", "name"):
            value = claims.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    stored_username = token_data.get("username")
    if isinstance(stored_username, str) and stored_username.strip() and stored_username != "azure-user":
        return stored_username.strip()

    client_info = _decode_base64_json(token_data.get("client_info", ""))
    for key in ("uid", "utid"):
        value = client_info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for claims in _azure_claim_sets(token_data):
        for key in ("oid", "sub"):
            value = claims.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _azure_identity_claims(token_data: dict[str, Any]) -> dict[str, Any]:
    claims = token_data.get("id_token_claims")
    if isinstance(claims, dict):
        return claims
    return _decode_jwt_claims(token_data.get("id_token", ""))


def _azure_claim_sets(token_data: dict[str, Any]) -> list[dict[str, Any]]:
    claim_sets = []
    id_claims = _azure_identity_claims(token_data)
    if id_claims:
        claim_sets.append(id_claims)
    access_claims = _decode_jwt_claims(token_data.get("access_token", ""))
    if access_claims:
        claim_sets.append(access_claims)
    return claim_sets


def _decode_base64_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        value += "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    if not token or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


async def ensure_azure_cli_profile(
    user_id: UUID,
    token_data: dict[str, Any],
    subscriptions_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Persist an isolated Azure CLI profile/cache for the connected user."""
    if not token_data.get("access_token"):
        return {"ready": False, "message": "Azure is not connected for this user."}

    username = extract_azure_username(token_data)
    if not username:
        return {
            "ready": False,
            "message": (
                "Azure sign-in returned an access token but no usable user identity. "
                "Reconnect Azure so the platform can store a user-scoped CLI session."
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
        return {"ok": False, "message": str(exc)}


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

    response = {
        "token_type": token_data.get("token_type") or "Bearer",
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "id_token": token_data.get("id_token"),
        "id_token_claims": _azure_identity_claims(token_data),
        "client_info": token_data.get("client_info"),
        "scope": token_data.get("scope") or azure_device_scope_string(),
        "expires_in": int(token_data.get("expires_in") or max(_expires_on(token_data) - int(time.time()), 0) or 3600),
    }
    response = {key: value for key, value in response.items() if value}
    event = {
        "client_id": token_data.get("client_id") or AZURE_CLI_CLIENT_ID,
        "scope": (token_data.get("scope") or azure_device_scope_string()).split(),
        "token_endpoint": AZURE_TOKEN_ENDPOINT,
        "environment": "login.microsoftonline.com",
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "response": response,
        "data": {"username": token_data.get("username") or extract_azure_username(token_data)},
    }
    cache.add(event)
    _atomic_write(cache_path, cache.serialize(), mode=0o600)


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


async def diagnose_azure_connection(user_id: Optional[UUID]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await _get_fresh_azure_token(user_id) if user_id else None
    access_token = token_data.get("access_token") if token_data else None
    if not access_token:
        return {
            "status": "failed",
            "connector": "azure_cli",
            "request_id": request_id,
            "message": "Azure is not connected for this user.",
        }
    if _token_expired(token_data):
        return {
            "status": "failed",
            "connector": "azure_cli",
            "request_id": request_id,
            "message": "Azure token is expired. Reconnect Azure for this user.",
        }

    try:
        subscriptions_result = await _list_azure_subscriptions(access_token)
        if not subscriptions_result.get("ok"):
            return {
                "status": "failed",
                "connector": "azure_cli",
                "request_id": request_id,
                "message": subscriptions_result.get("message", "Azure token check failed."),
                "stderr": subscriptions_result.get("stderr", ""),
            }
        subscriptions = subscriptions_result.get("subscriptions", [])
        profile = await ensure_azure_cli_profile(user_id, token_data, subscriptions_result) if user_id else {"ready": False}
        if not profile.get("ready"):
            return {
                "status": "failed",
                "connector": "azure_cli",
                "request_id": request_id,
                "message": profile.get("message", "Azure CLI profile could not be prepared for this user."),
                "cli_profile_ready": False,
                "subscriptions": [
                    {
                        "subscription_id": sub.get("subscriptionId"),
                        "display_name": sub.get("displayName"),
                        "state": sub.get("state"),
                    }
                    for sub in subscriptions[:10]
                ],
            }
        cli_check = await validate_azure_cli_profile(user_id) if user_id else {"ready": False}
        if not cli_check.get("ready"):
            return {
                "status": "failed",
                "connector": "azure_cli",
                "request_id": request_id,
                "message": cli_check.get("message", "Azure CLI profile validation failed."),
                "stderr": cli_check.get("stderr", ""),
                "cli_profile_ready": False,
                "subscriptions": [
                    {
                        "subscription_id": sub.get("subscriptionId"),
                        "display_name": sub.get("displayName"),
                        "state": sub.get("state"),
                    }
                    for sub in subscriptions[:10]
                ],
            }
        return {
            "status": "success",
            "connector": "azure_cli",
            "request_id": request_id,
            "message": f"Azure token is valid. Visible subscriptions: {len(subscriptions)}.",
            "cli_profile_ready": bool(profile.get("ready")),
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


async def validate_azure_cli_profile(user_id: UUID, timeout: int = 20) -> dict[str, Any]:
    env = {
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_CONFIG_DIR": _azure_config_dir(user_id),
    }
    result = await run_command(
        "az account get-access-token --resource https://management.core.windows.net/ --only-show-errors -o json",
        timeout=timeout,
        env=env,
    )
    if result.success:
        return {"ready": True, "stdout": result.stdout}
    output = result.to_dict()
    return {
        "ready": False,
        "message": output.get("error") or output.get("stderr") or "Azure CLI profile validation failed.",
        "stderr": output.get("stderr", ""),
        "exit_code": output.get("exit_code"),
    }


async def run_github_cli_command(command: str, user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await retrieve_token("github", user_id) if user_id else None
    access_token = token_data.get("access_token") if token_data else None
    normalized = command.strip()
    if not access_token:
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "timed_out": False,
            "output_truncated": False,
            "stdout_chars": 0,
            "stderr_chars": 0,
            "error": "GitHub is not connected for this user.",
            "command": normalized,
            "connector": "github_cli",
            "request_id": request_id,
            "status": "failed",
            "auth_method": "not_connected",
        }

    env: dict[str, str] = {}
    env.update({"GH_TOKEN": access_token, "GITHUB_TOKEN": access_token})

    result = await run_command(normalized, timeout=timeout, env=env)
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": "github_cli",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "oauth_token_env",
    })
    return output


async def diagnose_github_connection(user_id: Optional[UUID]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await retrieve_token("github", user_id) if user_id else None
    access_token = token_data.get("access_token") if token_data else None
    if not access_token:
        return {
            "status": "failed",
            "connector": "github_cli",
            "request_id": request_id,
            "message": "GitHub is not connected for this user.",
        }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if response.status_code >= 400:
            return {
                "status": "failed",
                "connector": "github_cli",
                "request_id": request_id,
                "message": f"GitHub token check failed with HTTP {response.status_code}.",
                "stderr": response.text[:1000],
            }
        user = response.json()
        return {
            "status": "success",
            "connector": "github_cli",
            "request_id": request_id,
            "message": f"GitHub token is valid for {user.get('login', 'the connected user')}.",
            "login": user.get("login"),
            "scopes": response.headers.get("X-OAuth-Scopes", ""),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "connector": "github_cli",
            "request_id": request_id,
            "message": f"GitHub diagnostics failed: {exc}",
        }
