"""User-scoped command helpers for native Microsoft Admin and GitHub connectors."""
import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import re
import shlex
import time
import uuid
from typing import Any, Optional
from uuid import UUID

import httpx

from app.services.ops_command_runner import run_command
from app.services.token_storage import retrieve_token, store_token


logger = logging.getLogger(__name__)
TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")
AZURE_CLI_CLIENT_ID = os.environ.get("AZURE_CLI_CLIENT_ID", "04b07795-8ddb-461a-bbee-02f9e1bf7b46")
AZURE_AUTHORITY_HOST = os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com")
AZURE_TOKEN_ENDPOINT = f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/v2.0/token"
AZURE_ARM_SCOPE = os.environ.get("AZURE_ARM_SCOPE", "https://management.core.windows.net//.default")
AZURE_DEVICE_SCOPES = [AZURE_ARM_SCOPE, "openid", "profile", "offline_access"]
AZURE_ENVIRONMENT_NAME = os.environ.get("AZURE_ENVIRONMENT_NAME", "AzureCloud")
MICROSOFT_GRAPH_SCOPE = os.environ.get("MICROSOFT_GRAPH_SCOPE", "https://graph.microsoft.com/.default")
MICROSOFT_GRAPH_BASE_URL = os.environ.get("MICROSOFT_GRAPH_BASE_URL", "https://graph.microsoft.com")
EXCHANGE_ONLINE_SCOPE = os.environ.get("EXCHANGE_ONLINE_SCOPE", "https://outlook.office365.com/.default")
GITHUB_HOST = os.environ.get("GITHUB_HOST", "github.com")
MS_ADMIN_ALLOWED_BINARIES = {"az", "pwsh", "bicep"}
MS_ADMIN_FORBIDDEN_COMMAND_RE = re.compile(r"(?i)(^|[\s;&|`])(gh|git)(\.exe)?($|[\s;&|])")
GITHUB_ALLOWED_BINARIES = {"gh", "git", "jq", "rg", "which"}
MICROSOFT_ADMIN_SCOPE_PROFILES = {
    "arm": AZURE_ARM_SCOPE,
    "graph": MICROSOFT_GRAPH_SCOPE,
    "exchange": EXCHANGE_ONLINE_SCOPE,
}


def _normalize_azure_command(command: str) -> str:
    command = command.strip()
    return command if command.startswith("az ") else f"az {command}"


def azure_device_scope_string() -> str:
    return " ".join(AZURE_DEVICE_SCOPES)


def microsoft_admin_scope_profile(profile: str | None) -> str:
    normalized = str(profile or "arm").strip().lower()
    return normalized if normalized in MICROSOFT_ADMIN_SCOPE_PROFILES else "arm"


def microsoft_admin_device_scope_string(profile: str | None = None) -> str:
    """Return a single-resource device-code scope string for a Microsoft Admin consent profile."""
    scope_profile = microsoft_admin_scope_profile(profile)
    return f"{MICROSOFT_ADMIN_SCOPE_PROFILES[scope_profile]} openid profile offline_access"


def azure_token_request_data() -> dict[str, str]:
    """Return token request fields that mirror Azure CLI/MSAL device auth."""
    return {"scope": azure_device_scope_string(), "client_info": "1"}


async def _run_ms_admin_azure_cli(command: str, user_id: Optional[UUID], timeout: int, request_id: str) -> dict[str, Any]:
    normalized = _normalize_azure_command(command)
    token_data = await _get_fresh_azure_token(user_id) if user_id else None
    if not token_data or not token_data.get("access_token"):
        result = _failed_ms_admin_result(
            request_id=request_id,
            mode="azure_cli",
            message="Microsoft Admin is not connected for this user.",
            command=normalized,
            error_type="not_connected",
        )
        result["auth_method"] = "not_connected"
        return result
    if _token_expired(token_data):
        result = _failed_ms_admin_result(
            request_id=request_id,
            mode="azure_cli",
            message="Microsoft Admin token is expired. Reconnect Microsoft Admin for this user.",
            command=normalized,
            error_type="expired_user_token",
        )
        result["auth_method"] = "expired_user_token"
        return result

    profile = await ensure_azure_cli_profile(user_id, token_data)
    if not profile.get("ready"):
        result = _failed_ms_admin_result(
            request_id=request_id,
            mode="azure_cli",
            message=profile.get("message", "Microsoft Admin Azure CLI profile could not be prepared for this user."),
            command=normalized,
            error_type="profile_not_ready",
        )
        result["auth_method"] = "user_scoped_microsoft_admin_shell"
        return result

    env: dict[str, str] = {
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_CONFIG_DIR": _azure_config_dir(user_id),
    }

    result = await run_command(
        normalized,
        timeout=timeout,
        env=env,
        allowed_binaries=MS_ADMIN_ALLOWED_BINARIES,
    )
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": "ms_admin",
        "mode": "azure_cli",
        "subtool": "azure_cli",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "user_scoped_microsoft_admin_shell",
    })
    return output


def _failed_ms_admin_result(
    *,
    request_id: str,
    mode: str,
    message: str,
    command: str = "",
    error_type: str = "invalid_tool_arguments",
) -> dict[str, Any]:
    return {
        "stdout": "",
        "stderr": "",
        "exit_code": 1,
        "timed_out": False,
        "output_truncated": False,
        "stdout_chars": 0,
        "stderr_chars": 0,
        "error": message,
        "error_type": error_type,
        "command": command,
        "connector": "ms_admin",
        "mode": mode,
        "request_id": request_id,
        "status": "failed",
    }


async def run_ms_admin_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute the consolidated Microsoft Admin connector tool.

    The connector intentionally excludes GitHub tooling. GitHub remains a
    separate connector because it has a separate OAuth/token model and audit
    surface.
    """
    request_id = uuid.uuid4().hex[:16]
    mode = str(arguments.get("mode") or "").strip().lower() or "status"
    try:
        timeout_value = int(arguments.get("timeout") or timeout or 60)
    except (TypeError, ValueError):
        timeout_value = 60
    timeout = max(1, min(timeout_value, 300))

    if mode in {"status", "health"}:
        return await _ms_admin_status(user_id, request_id)

    if mode == "azure_cli":
        command = str(arguments.get("command") or "").strip()
        if not command:
            return _failed_ms_admin_result(request_id=request_id, mode=mode, message="Provide command for azure_cli mode.")
        return await _run_ms_admin_azure_cli(command, user_id, timeout=timeout, request_id=request_id)

    if mode in {"powershell", "pwsh"}:
        script = str(arguments.get("script") or arguments.get("command") or "").strip()
        if not script:
            return _failed_ms_admin_result(request_id=request_id, mode=mode, message="Provide script or command for powershell mode.")
        if _ms_admin_forbidden_command(script):
            return _failed_ms_admin_result(
                request_id=request_id,
                mode=mode,
                message="GitHub commands are not available in the Microsoft Admin connector. Use the GitHub connector.",
                command=script,
                error_type="unsupported_command",
            )
        return await _run_ms_admin_powershell(script, user_id, timeout=timeout, request_id=request_id)

    if mode == "bicep":
        command = str(arguments.get("command") or "").strip()
        if not command:
            return _failed_ms_admin_result(request_id=request_id, mode=mode, message="Provide command for bicep mode.")
        return await _run_ms_admin_bicep(command, user_id, timeout=timeout, request_id=request_id)

    if mode == "graph_request":
        return await _run_ms_admin_graph_request(arguments, user_id, request_id=request_id)

    return _failed_ms_admin_result(
        request_id=request_id,
        mode=mode,
        message="mode must be one of: status, azure_cli, powershell, bicep, graph_request.",
    )


def _ms_admin_forbidden_command(script: str) -> bool:
    return bool(MS_ADMIN_FORBIDDEN_COMMAND_RE.search(script))


def _ms_admin_home_dir(user_id: UUID) -> str:
    base = os.environ.get("MS_ADMIN_USER_HOME_ROOT", "/tmp/ai-platform-ms-admin")
    path = os.path.join(base, user_id.hex)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _ms_admin_env(user_id: UUID) -> dict[str, str]:
    return {
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_CONFIG_DIR": _azure_config_dir(user_id),
        "HOME": _ms_admin_home_dir(user_id),
    }


async def _run_ms_admin_powershell(script: str, user_id: Optional[UUID], timeout: int, request_id: str) -> dict[str, Any]:
    token_data = await _get_fresh_azure_token(user_id) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return _failed_ms_admin_result(
            request_id=request_id,
            mode="powershell",
            message="Microsoft Admin is not connected for this user.",
            command=script,
            error_type="not_connected",
        )
    profile = await ensure_azure_cli_profile(user_id, token_data) if user_id else {"ready": False}
    if not profile.get("ready"):
        return _failed_ms_admin_result(
            request_id=request_id,
            mode="powershell",
            message=profile.get("message", "Microsoft Admin shell profile could not be prepared for this user."),
            command=script,
            error_type="profile_not_ready",
        )

    env = _ms_admin_env(user_id)
    env["AI_PLATFORM_MS_USERNAME"] = extract_azure_username(token_data)
    env["AI_PLATFORM_ARM_ACCESS_TOKEN"] = token_data.get("access_token", "")
    graph_token = await _get_fresh_azure_token_for_scope(user_id, MICROSOFT_GRAPH_SCOPE) if user_id else None
    if graph_token and graph_token.get("access_token") and not graph_token.get("refresh_error"):
        env["AI_PLATFORM_GRAPH_ACCESS_TOKEN"] = graph_token["access_token"]
    exchange_token = await _get_fresh_azure_token_for_scope(user_id, EXCHANGE_ONLINE_SCOPE) if user_id else None
    if exchange_token and exchange_token.get("access_token") and not exchange_token.get("refresh_error"):
        env["AI_PLATFORM_EXCHANGE_ACCESS_TOKEN"] = exchange_token["access_token"]

    full_script = f"{_ms_admin_powershell_preamble()}\n{script}"
    result = await run_command(
        f"pwsh -NoLogo -NoProfile -NonInteractive -Command {shlex.quote(full_script)}",
        timeout=timeout,
        env=env,
        allowed_binaries=MS_ADMIN_ALLOWED_BINARIES,
    )
    output = result.to_dict()
    output.update({
        "command": script,
        "connector": "ms_admin",
        "mode": "powershell",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "user_scoped_microsoft_admin_shell",
    })
    return output


def _ms_admin_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformAz {
    if (-not $env:AI_PLATFORM_ARM_ACCESS_TOKEN) { throw 'Microsoft Admin ARM token is not available.' }
    Import-Module Az.Accounts -ErrorAction Stop
    $secureToken = ConvertTo-SecureString $env:AI_PLATFORM_ARM_ACCESS_TOKEN -AsPlainText -Force
    Connect-AzAccount -AccessToken $secureToken -AccountId $env:AI_PLATFORM_MS_USERNAME -Tenant $env:AZURE_TENANT_ID | Out-Null
}
function Connect-AIPlatformGraph {
    if (-not $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN) { throw 'Microsoft Graph token is not available. Reconnect Microsoft Admin with Graph consent.' }
    Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
    $secureToken = ConvertTo-SecureString $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN -AsPlainText -Force
    Connect-MgGraph -AccessToken $secureToken -NoWelcome
}
function Connect-AIPlatformExchange {
    if (-not $env:AI_PLATFORM_EXCHANGE_ACCESS_TOKEN) { throw 'Exchange Online token is not available. Reconnect Microsoft Admin with Exchange consent.' }
    Import-Module ExchangeOnlineManagement -ErrorAction Stop
    Connect-ExchangeOnline -AccessToken $env:AI_PLATFORM_EXCHANGE_ACCESS_TOKEN -UserPrincipalName $env:AI_PLATFORM_MS_USERNAME -ShowBanner:$false
}
function Connect-AIPlatformTeams {
    if (-not $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN) { throw 'Microsoft Graph token is not available. Reconnect Microsoft Admin with Graph consent.' }
    Import-Module MicrosoftTeams -ErrorAction Stop
    Connect-MicrosoftTeams -AccessTokens @($env:AI_PLATFORM_GRAPH_ACCESS_TOKEN) | Out-Null
}
"""


async def _run_ms_admin_bicep(command: str, user_id: Optional[UUID], timeout: int, request_id: str) -> dict[str, Any]:
    if _ms_admin_forbidden_command(command):
        return _failed_ms_admin_result(
            request_id=request_id,
            mode="bicep",
            message="GitHub commands are not available in the Microsoft Admin connector. Use the GitHub connector.",
            command=command,
            error_type="unsupported_command",
        )
    normalized = command if command.startswith("bicep ") else f"bicep {command}"
    env = _ms_admin_env(user_id) if user_id else {}
    result = await run_command(
        normalized,
        timeout=timeout,
        env=env,
        allowed_binaries=MS_ADMIN_ALLOWED_BINARIES,
    )
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": "ms_admin",
        "mode": "bicep",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "local_bicep_cli",
    })
    return output


async def _run_ms_admin_graph_request(arguments: dict[str, Any], user_id: Optional[UUID], request_id: str) -> dict[str, Any]:
    method = str(arguments.get("method") or "GET").strip().upper()
    path = str(arguments.get("path") or "").strip()
    api_version = str(arguments.get("api_version") or "v1.0").strip().strip("/")
    if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
        return _failed_ms_admin_result(request_id=request_id, mode="graph_request", message="Unsupported Graph method.")
    if not path.startswith("/"):
        return _failed_ms_admin_result(request_id=request_id, mode="graph_request", message="Graph path must start with '/'.")

    token_data = await _get_fresh_azure_token_for_scope(user_id, MICROSOFT_GRAPH_SCOPE) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return {
            "status": "failed",
            "connector": "ms_admin",
            "mode": "graph_request",
            "request_id": request_id,
            "error_type": "not_connected",
            "message": "Microsoft Graph token is not available. Reconnect Microsoft Admin with Graph consent.",
            "refresh_error": token_data.get("refresh_error") if token_data else None,
        }

    url = f"{MICROSOFT_GRAPH_BASE_URL.rstrip('/')}/{api_version}{path}"
    headers = {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Content-Type": "application/json",
    }
    extra_headers = arguments.get("headers")
    if isinstance(extra_headers, dict):
        headers.update({str(k): str(v) for k, v in extra_headers.items()})
    body = arguments.get("body")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(method, url, headers=headers, json=body if body is not None else None)
        try:
            data: Any = response.json()
        except Exception:
            data = response.text
        return {
            "status": "success" if response.status_code < 400 else "failed",
            "connector": "ms_admin",
            "mode": "graph_request",
            "request_id": request_id,
            "method": method,
            "path": path,
            "api_version": api_version,
            "status_code": response.status_code,
            "result": data,
        }
    except Exception as exc:
        logger.warning("Microsoft Graph request failed for request_id=%s: %s", request_id, exc)
        return {
            "status": "failed",
            "connector": "ms_admin",
            "mode": "graph_request",
            "request_id": request_id,
            "error_type": "graph_request_failed",
            "message": "Microsoft Graph request failed. Check connector logs with this request_id.",
        }


async def _ms_admin_status(user_id: Optional[UUID], request_id: str) -> dict[str, Any]:
    diagnosis = await diagnose_azure_connection(user_id)
    return {
        **diagnosis,
        "connector": "ms_admin",
        "mode": "status",
        "request_id": request_id,
        "tooling": {
            "powershell_7": "pwsh",
            "graph_powershell": "Microsoft.Graph",
            "exchange_online_powershell": "ExchangeOnlineManagement",
            "teams_powershell": "MicrosoftTeams",
            "pnp_powershell": "PnP.PowerShell",
            "az_powershell": "Az",
            "azure_cli": "az",
            "bicep_cli": "bicep",
            "direct_graph": "https://graph.microsoft.com",
            "powershell_helpers": [
                "Connect-AIPlatformAz",
                "Connect-AIPlatformGraph",
                "Connect-AIPlatformExchange",
                "Connect-AIPlatformTeams",
            ],
        },
        "notes": [
            "GitHub CLI is intentionally excluded; use the GitHub connector.",
            "PowerShell module access is controlled by the signed-in Microsoft user's permissions and consented scopes.",
        ],
    }


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
        logger.warning("Azure token refresh failed for user %s: %s", user_id.hex[:12], exc)
        return {**token_data, "refresh_error": "token_refresh_failed"}


async def _get_fresh_azure_token_for_scope(user_id: Optional[UUID], scope: str) -> Optional[dict[str, Any]]:
    """Return a fresh Microsoft token for a non-ARM scope without replacing the ARM CLI cache."""
    if not user_id:
        return None
    token_data = await retrieve_token("azure", user_id)
    if not token_data:
        return None
    scope_profile = _scope_profile_for_scope(scope)
    cached_token = (token_data.get("delegated_tokens") or {}).get(scope_profile) if scope_profile else None
    if isinstance(cached_token, dict):
        expires_on = _expires_on(cached_token)
        if cached_token.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
            return {**token_data, **cached_token}

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return {**token_data, "refresh_error": "Stored Microsoft Admin token has no refresh token."}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                AZURE_TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": token_data.get("client_id") or AZURE_CLI_CLIENT_ID,
                    "refresh_token": refresh_token,
                    "scope": f"{scope} openid profile offline_access",
                    "client_info": "1",
                },
            )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            return {**token_data, "refresh_error": data.get("error_description") or data.get("error") or response.text[:500]}
        scoped_token = {
            **token_data,
            "client_id": token_data.get("client_id") or AZURE_CLI_CLIENT_ID,
            "token_type": data.get("token_type", token_data.get("token_type")),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token", refresh_token),
            "scope": data.get("scope", scope),
            "id_token": data.get("id_token", token_data.get("id_token")),
            "id_token_claims": _azure_identity_claims({"id_token": data.get("id_token")}) or token_data.get("id_token_claims"),
            "client_info": data.get("client_info", token_data.get("client_info")),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        if scope_profile:
            delegated_tokens = dict(token_data.get("delegated_tokens") or {})
            delegated_tokens[scope_profile] = {
                "token_type": scoped_token.get("token_type"),
                "access_token": scoped_token.get("access_token"),
                "scope": scoped_token.get("scope"),
                "expires_in": scoped_token.get("expires_in"),
                "expires_on": scoped_token.get("expires_on"),
            }
            consented = set(token_data.get("consented_scope_profiles") or [])
            consented.add(scope_profile)
            await store_token(
                "azure",
                user_id,
                {
                    **token_data,
                    "refresh_token": scoped_token.get("refresh_token", refresh_token),
                    "delegated_tokens": delegated_tokens,
                    "consented_scope_profiles": sorted(consented),
                },
            )
        return scoped_token
    except Exception as exc:
        logger.warning("Microsoft scoped token refresh failed for user %s scope=%s: %s", user_id.hex[:12], scope, exc)
        return {**token_data, "refresh_error": "token_refresh_failed"}


def _scope_profile_for_scope(scope: str) -> str:
    for profile, configured_scope in MICROSOFT_ADMIN_SCOPE_PROFILES.items():
        if scope == configured_scope:
            return profile
    return ""


async def get_fresh_azure_token(user_id: Optional[UUID]) -> Optional[dict[str, Any]]:
    """Return a stored Azure token, refreshing it first when possible."""
    return await _get_fresh_azure_token(user_id)


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
            "connector": "ms_admin",
            "request_id": request_id,
            "message": "Microsoft Admin is not connected for this user.",
        }
    if _token_expired(token_data):
        return {
            "status": "failed",
            "connector": "ms_admin",
            "request_id": request_id,
            "message": "Microsoft Admin token is expired. Reconnect Microsoft Admin for this user.",
        }

    try:
        subscriptions_result = await _list_azure_subscriptions(access_token)
        if not subscriptions_result.get("ok"):
            return {
                "status": "failed",
                "connector": "ms_admin",
                "request_id": request_id,
                "message": subscriptions_result.get("message", "Microsoft Admin token check failed."),
                "stderr": subscriptions_result.get("stderr", ""),
            }
        subscriptions = subscriptions_result.get("subscriptions", [])
        profile = await ensure_azure_cli_profile(user_id, token_data, subscriptions_result) if user_id else {"ready": False}
        if not profile.get("ready"):
            return {
                "status": "failed",
                "connector": "ms_admin",
                "request_id": request_id,
                "message": profile.get("message", "Microsoft Admin Azure CLI profile could not be prepared for this user."),
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
                "connector": "ms_admin",
                "request_id": request_id,
                "message": cli_check.get("message", "Microsoft Admin Azure CLI profile validation failed."),
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
            "connector": "ms_admin",
            "request_id": request_id,
            "message": f"Microsoft Admin token is valid. Visible Azure subscriptions: {len(subscriptions)}.",
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
        logger.warning("Azure diagnostics failed for request_id=%s: %s", request_id, exc)
        return {
            "status": "failed",
            "connector": "ms_admin",
            "request_id": request_id,
            "message": "Microsoft Admin diagnostics failed. Check connector logs with this request_id.",
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
        allowed_binaries=MS_ADMIN_ALLOWED_BINARIES,
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

    profile = await ensure_github_cli_profile(user_id, token_data) if user_id else {"ready": False}
    if not profile.get("ready"):
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "timed_out": False,
            "output_truncated": False,
            "stdout_chars": 0,
            "stderr_chars": 0,
            "error": profile.get("message", "GitHub CLI profile could not be prepared for this user."),
            "command": normalized,
            "connector": "github_cli",
            "request_id": request_id,
            "status": "failed",
            "auth_method": "user_scoped_gh_cli",
        }

    env: dict[str, str] = {"GH_CONFIG_DIR": _github_config_dir(user_id)}

    result = await run_command(
        normalized,
        timeout=timeout,
        env=env,
        allowed_binaries=GITHUB_ALLOWED_BINARIES,
    )
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": "github_cli",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "user_scoped_gh_cli",
    })
    return output


def _github_config_dir(user_id: UUID) -> str:
    base = os.environ.get("GITHUB_CLI_USER_CONFIG_ROOT", "/tmp/ai-platform-github-cli")
    path = os.path.join(base, user_id.hex)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


async def _fetch_github_user(access_token: str) -> dict[str, Any]:
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
                "ok": False,
                "message": f"GitHub token check failed with HTTP {response.status_code}.",
                "stderr": response.text[:1000],
            }
        user = response.json()
        return {
            "ok": True,
            "login": user.get("login"),
            "scopes": response.headers.get("X-OAuth-Scopes", ""),
        }
    except Exception as exc:
        return {"ok": False, "message": f"GitHub diagnostics failed: {exc}"}


async def ensure_github_cli_profile(
    user_id: UUID,
    token_data: dict[str, Any],
    login: Optional[str] = None,
) -> dict[str, Any]:
    access_token = token_data.get("access_token")
    if not access_token:
        return {"ready": False, "message": "GitHub is not connected for this user."}

    login = login or token_data.get("login") or token_data.get("username")
    if not login:
        user_result = await _fetch_github_user(access_token)
        if not user_result.get("ok"):
            return {"ready": False, "message": user_result.get("message", "GitHub token check failed.")}
        login = user_result.get("login")

    await asyncio.to_thread(_write_github_cli_files, _github_config_dir(user_id), access_token, login or "")
    return {"ready": True, "login": login, "config_dir": _github_config_dir(user_id)}


def _write_github_cli_files(config_dir: str, access_token: str, login: str) -> None:
    path = Path(config_dir)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    hosts = (
        f"{GITHUB_HOST}:\n"
        f"    oauth_token: {json.dumps(access_token)}\n"
        f"    user: {json.dumps(login)}\n"
        "    git_protocol: https\n"
    )
    _atomic_write(path / "hosts.yml", hosts, mode=0o600)


async def validate_github_cli_profile(user_id: UUID, timeout: int = 20) -> dict[str, Any]:
    env = {"GH_CONFIG_DIR": _github_config_dir(user_id)}
    result = await run_command(
        f"gh api user --jq .login --hostname {GITHUB_HOST}",
        timeout=timeout,
        env=env,
        allowed_binaries=GITHUB_ALLOWED_BINARIES,
    )
    if result.success:
        return {"ready": True, "login": result.stdout.strip()}
    output = result.to_dict()
    return {
        "ready": False,
        "message": output.get("error") or output.get("stderr") or "GitHub CLI profile validation failed.",
        "stderr": output.get("stderr", ""),
        "exit_code": output.get("exit_code"),
    }


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

    user_result = await _fetch_github_user(access_token)
    if not user_result.get("ok"):
        return {
            "status": "failed",
            "connector": "github_cli",
            "request_id": request_id,
            "message": user_result.get("message", "GitHub token check failed."),
            "stderr": user_result.get("stderr", ""),
        }

    if user_id:
        profile = await ensure_github_cli_profile(user_id, token_data, login=user_result.get("login"))
        if not profile.get("ready"):
            return {
                "status": "failed",
                "connector": "github_cli",
                "request_id": request_id,
                "message": profile.get("message", "GitHub CLI profile could not be prepared for this user."),
                "cli_profile_ready": False,
                "login": user_result.get("login"),
                "scopes": user_result.get("scopes", ""),
            }
        cli_check = await validate_github_cli_profile(user_id)
        if not cli_check.get("ready"):
            return {
                "status": "failed",
                "connector": "github_cli",
                "request_id": request_id,
                "message": cli_check.get("message", "GitHub CLI profile validation failed."),
                "stderr": cli_check.get("stderr", ""),
                "cli_profile_ready": False,
                "login": user_result.get("login"),
                "scopes": user_result.get("scopes", ""),
            }

    return {
        "status": "success",
        "connector": "github_cli",
        "request_id": request_id,
        "message": f"GitHub token and CLI profile are valid for {user_result.get('login', 'the connected user')}.",
        "login": user_result.get("login"),
        "scopes": user_result.get("scopes", ""),
        "cli_profile_ready": True,
    }
