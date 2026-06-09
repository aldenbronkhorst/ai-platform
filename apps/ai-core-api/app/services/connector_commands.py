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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import httpx

from app.services.ops_command_runner import run_command
from app.services.token_storage import retrieve_token, store_token


logger = logging.getLogger(__name__)
MICROSOFT_ADMIN_PROVIDER = "microsoft_admin"


def _scope_values_from_env(env_name: str, default_values: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default_values
    values = tuple(part for part in re.split(r"[\s,;]+", raw) if part)
    return values or default_values


TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")
MICROSOFT_ADMIN_CLIENT_ID = (
    os.environ.get("MICROSOFT_ADMIN_CLIENT_ID")
    or os.environ.get("MS_ADMIN_CLIENT_ID")
    or "8a178920-de9e-41cf-af4e-c3012fc3bbd2"
)
MICROSOFT_ADMIN_APP_DISPLAY_NAME = os.environ.get("MICROSOFT_ADMIN_APP_DISPLAY_NAME", "AI Platform Microsoft Admin")
AZURE_AUTHORITY_HOST = os.environ.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com")
AZURE_TOKEN_ENDPOINT = f"{AZURE_AUTHORITY_HOST.rstrip('/')}/{TENANT_ID}/oauth2/v2.0/token"
AZURE_ARM_SCOPE = os.environ.get("AZURE_ARM_SCOPE", "https://management.azure.com/user_impersonation")
AZURE_ENVIRONMENT_NAME = os.environ.get("AZURE_ENVIRONMENT_NAME", "AzureCloud")
MICROSOFT_ADMIN_PRIMARY_SCOPE_PROFILE = "graph"
DEFAULT_MICROSOFT_GRAPH_SCOPES = (
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/User.ReadWrite.All",
    "https://graph.microsoft.com/Directory.ReadWrite.All",
    "https://graph.microsoft.com/Group.ReadWrite.All",
    "https://graph.microsoft.com/Organization.Read.All",
    "https://graph.microsoft.com/RoleManagement.ReadWrite.Directory",
    "https://graph.microsoft.com/Application.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementManagedDevices.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementConfiguration.ReadWrite.All",
    "https://graph.microsoft.com/DeviceManagementApps.ReadWrite.All",
    "https://graph.microsoft.com/Policy.ReadWrite.ConditionalAccess",
    "https://graph.microsoft.com/Sites.FullControl.All",
    "https://graph.microsoft.com/Reports.Read.All",
    "https://graph.microsoft.com/AuditLog.Read.All",
)
MICROSOFT_GRAPH_SCOPES = _scope_values_from_env(
    "MICROSOFT_GRAPH_SCOPES",
    _scope_values_from_env("MICROSOFT_GRAPH_SCOPE", DEFAULT_MICROSOFT_GRAPH_SCOPES),
)
MICROSOFT_GRAPH_SCOPE = " ".join(MICROSOFT_GRAPH_SCOPES)
MICROSOFT_GRAPH_BASE_URL = os.environ.get("MICROSOFT_GRAPH_BASE_URL", "https://graph.microsoft.com")
EXCHANGE_ONLINE_SCOPE = os.environ.get("EXCHANGE_ONLINE_SCOPE", "https://outlook.office365.com/.default")
EXCHANGE_ONLINE_SCOPES = _scope_values_from_env("EXCHANGE_ONLINE_SCOPES", (EXCHANGE_ONLINE_SCOPE,))
TEAMS_TENANT_ADMIN_SCOPE = os.environ.get(
    "TEAMS_TENANT_ADMIN_SCOPE",
    "48ac35b8-9aa8-4d74-927d-1f4a14a0b239/.default",
)
GITHUB_HOST = os.environ.get("GITHUB_HOST", "github.com")
MS_AZURE_CLI_ALLOWED_BINARIES = {"az"}
MS_POWERSHELL_ALLOWED_BINARIES = {"pwsh"}
MS_BICEP_ALLOWED_BINARIES = {"bicep"}
MS_ADMIN_ALLOWED_BINARIES = MS_AZURE_CLI_ALLOWED_BINARIES | MS_POWERSHELL_ALLOWED_BINARIES | MS_BICEP_ALLOWED_BINARIES
MS_ADMIN_FORBIDDEN_COMMAND_RE = re.compile(r"(?i)(^|[\s;&|`])(gh|git)(\.exe)?($|[\s;&|])")
GITHUB_ALLOWED_BINARIES = {"gh", "git", "jq", "rg", "which"}
MICROSOFT_ADMIN_SCOPE_PROFILES = {
    "arm": (AZURE_ARM_SCOPE,),
    "graph": MICROSOFT_GRAPH_SCOPES,
    "exchange": EXCHANGE_ONLINE_SCOPES,
    "teams": (TEAMS_TENANT_ADMIN_SCOPE,),
    "sharepoint": (),
}
MICROSOFT_ADMIN_SCOPE_PROFILE_LABELS = {
    "arm": "Azure Resource Manager",
    "graph": "Microsoft Graph Admin",
    "exchange": "Exchange Online",
    "teams": "Teams Admin",
    "sharepoint": "SharePoint / PnP",
}
GRAPH_AUTO_PAGE_MAX_PAGES = 20
GRAPH_AUTO_PAGE_MAX_ITEMS = 1000

def _normalize_azure_command(command: str) -> str:
    command = command.strip()
    return command if command.startswith("az ") else f"az {command}"


def _tool_timeout(arguments: dict[str, Any], default: int = 60) -> int:
    try:
        timeout_value = int(arguments.get("timeout") or default or 60)
    except (TypeError, ValueError):
        timeout_value = 60
    return max(1, min(timeout_value, 300))


def microsoft_admin_arm_device_scope_string() -> str:
    return microsoft_admin_device_scope_string("arm")


def microsoft_admin_scope_profile(profile: str | None) -> str:
    normalized = str(profile or MICROSOFT_ADMIN_PRIMARY_SCOPE_PROFILE).strip().lower()
    return normalized if normalized in MICROSOFT_ADMIN_SCOPE_PROFILES else MICROSOFT_ADMIN_PRIMARY_SCOPE_PROFILE


def microsoft_admin_scope_values(profile: str | None = None) -> list[str]:
    scope_profile = microsoft_admin_scope_profile(profile)
    return list(MICROSOFT_ADMIN_SCOPE_PROFILES[scope_profile])


def microsoft_admin_client_id_for_scope_profile(profile: str | None = None) -> str:
    return MICROSOFT_ADMIN_CLIENT_ID


def microsoft_admin_app_name_for_scope_profile(profile: str | None = None) -> str:
    return MICROSOFT_ADMIN_APP_DISPLAY_NAME


def microsoft_admin_token_client_error(token_data: dict[str, Any] | None) -> str:
    if not token_data:
        return ""
    client_id = str(token_data.get("client_id") or "").strip()
    if client_id == MICROSOFT_ADMIN_CLIENT_ID:
        return ""
    if not client_id:
        return "Stored Microsoft Admin token is missing its application identity. Reconnect Microsoft Admin."
    return "Stored Microsoft Admin token was issued for a retired application. Reconnect Microsoft Admin."


def _invalid_microsoft_admin_token(token_data: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "client_id": token_data.get("client_id"),
        "scope_profile": token_data.get("scope_profile"),
        "username": token_data.get("username"),
        "refresh_error": message,
        "error_type": "reconnect_required",
    }


def _microsoft_admin_scope_unavailable(
    token_data: dict[str, Any],
    scope_profile: str | None,
    message: str,
    error_type: str,
) -> dict[str, Any]:
    return {
        "client_id": token_data.get("client_id") or MICROSOFT_ADMIN_CLIENT_ID,
        "scope_profile": scope_profile,
        "username": token_data.get("username"),
        "refresh_error": message,
        "error_type": error_type,
    }


def _microsoft_admin_oauth_error_type(data: dict[str, Any]) -> str:
    error = str(data.get("error") or "").lower()
    description = str(data.get("error_description") or "").lower()
    if "aadsts65001" in description or "consent" in description:
        return "consent_required"
    if error == "invalid_grant":
        return "authorization_failed"
    return error or "token_refresh_failed"


def _microsoft_admin_oauth_error_message(scope_profile: str | None, data: dict[str, Any], fallback: str) -> str:
    label = microsoft_admin_scope_label(scope_profile) if scope_profile else "requested Microsoft scope"
    error_type = _microsoft_admin_oauth_error_type(data)
    if error_type == "consent_required":
        return (
            f"Tenant admin consent is required for {label}. "
            "Grant consent to the Microsoft Admin app once, then reconnect Microsoft Admin."
        )
    return data.get("error_description") or data.get("error") or fallback


def microsoft_admin_scope_label(profile: str | None = None) -> str:
    scope_profile = microsoft_admin_scope_profile(profile)
    return MICROSOFT_ADMIN_SCOPE_PROFILE_LABELS[scope_profile]


def microsoft_admin_scope_summary(profile: str | None = None) -> str:
    scope_profile = microsoft_admin_scope_profile(profile)
    if scope_profile == "sharepoint":
        return f"{microsoft_admin_scope_label(scope_profile)}: target SharePoint site .default"
    scope_names = [
        value.rsplit("/", 1)[-1]
        for value in microsoft_admin_scope_values(scope_profile)
    ]
    return f"{microsoft_admin_scope_label(scope_profile)}: {', '.join(scope_names)}"


def microsoft_admin_device_scope_string(profile: str | None = None) -> str:
    """Return a single-resource device-code scope string for a Microsoft Admin consent profile."""
    scope_profile = microsoft_admin_scope_profile(profile)
    return " ".join([*microsoft_admin_scope_values(scope_profile), "openid", "profile", "offline_access"])


def microsoft_admin_arm_token_request_data() -> dict[str, str]:
    """Return token request fields for the Microsoft Admin ARM profile."""
    return {"scope": microsoft_admin_arm_device_scope_string(), "client_info": "1"}


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
        allowed_binaries=allowed_binaries or MS_ADMIN_ALLOWED_BINARIES,
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


def _failed_microsoft_admin_result(
    *,
    request_id: str,
    mode: str,
    message: str,
    command: str = "",
    error_type: str = "invalid_tool_arguments",
    connector: str = "microsoft_admin",
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
        "message": message,
        "error_type": error_type,
        "command": command,
        "connector": connector,
        "mode": mode,
        "request_id": request_id,
        "status": "failed",
    }


def _command_failure_message(output: dict[str, Any], default: str) -> str:
    for key in ("error", "stderr", "stdout"):
        value = str(output.get(key) or "").strip()
        if value:
            first_line = next((line.strip() for line in value.splitlines() if line.strip()), value)
            return first_line[:500]
    return default


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


async def run_ms_graph_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute a direct Microsoft Graph request through the Microsoft Admin connector."""
    request_id = uuid.uuid4().hex[:16]
    return await _run_microsoft_admin_graph_request(arguments, user_id, request_id=request_id, connector_name="ms_graph")


def _prepare_microsoft_admin_powershell_script(
    arguments: dict[str, Any],
    timeout: int,
    *,
    connector_name: str,
) -> tuple[str, int, str, dict[str, Any] | None]:
    request_id = uuid.uuid4().hex[:16]
    bounded_timeout = _tool_timeout(arguments, timeout)
    script = str(arguments.get("script") or arguments.get("command") or "").strip()
    if not script:
        return request_id, bounded_timeout, script, _failed_microsoft_admin_result(
            request_id=request_id,
            mode=connector_name,
            message=f"Provide script for {connector_name}.",
            connector=connector_name,
        )
    if _microsoft_admin_forbidden_command(script):
        return request_id, bounded_timeout, script, _failed_microsoft_admin_result(
            request_id=request_id,
            mode=connector_name,
            message="GitHub commands are not available in the Microsoft Admin connector. Use the GitHub connector.",
            command=script,
            error_type="unsupported_command",
            connector=connector_name,
        )
    return request_id, bounded_timeout, script, None


async def _run_microsoft_admin_powershell_tool(
    script: str,
    user_id: Optional[UUID],
    timeout: int,
    request_id: str,
    *,
    connector_name: str,
    token_env: dict[str, str],
    preamble: str,
    required_env: tuple[str, ...],
) -> dict[str, Any]:
    if not user_id:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode=connector_name,
            message="Microsoft Admin is not connected for this user.",
            command=script,
            error_type="not_connected",
            connector=connector_name,
        )
    missing_env = [name for name in required_env if not token_env.get(name)]
    if missing_env:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode=connector_name,
            message=(
                f"{connector_name} authorization profile is not available. "
                "Reconnect Microsoft Admin and ensure tenant consent/user permissions for this Microsoft workload."
            ),
            command=script,
            error_type="authorization_profile_unavailable",
            connector=connector_name,
        )
    env = _microsoft_admin_env(user_id) if user_id else {}
    env.update(token_env)
    return await run_microsoft_pwsh_tool(
        user_id=user_id,
        tool_name=connector_name,
        script=script,
        timeout=timeout,
        request_id=request_id,
        env=env,
        preamble=preamble,
    )


async def run_ms_graph_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute Microsoft Graph PowerShell through the Microsoft Admin connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_graph_powershell",
    )
    if error:
        return error
    token = await get_microsoft_admin_token(user_id, "graph")
    token_env = _microsoft_admin_token_env(
        token,
        access_token_env="AI_PLATFORM_GRAPH_ACCESS_TOKEN",
        username_token=token,
    )
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_graph_powershell",
        token_env=token_env,
        preamble=_ms_graph_powershell_preamble(),
        required_env=("AI_PLATFORM_GRAPH_ACCESS_TOKEN",),
    )


async def run_ms_exchange_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute Exchange Online PowerShell through the Microsoft Admin connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_exchange_powershell",
    )
    if error:
        return error
    token = await get_microsoft_admin_token(user_id, "exchange")
    token_env = _microsoft_admin_token_env(
        token,
        access_token_env="AI_PLATFORM_EXCHANGE_ACCESS_TOKEN",
        username_token=token,
    )
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_exchange_powershell",
        token_env=token_env,
        preamble=_ms_exchange_powershell_preamble(),
        required_env=("AI_PLATFORM_EXCHANGE_ACCESS_TOKEN", "AI_PLATFORM_MS_USERNAME"),
    )


async def run_ms_teams_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute Microsoft Teams PowerShell through the Microsoft Admin connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_teams_powershell",
    )
    if error:
        return error
    graph_token = await get_microsoft_admin_token(user_id, "graph")
    teams_token = await get_microsoft_admin_token(user_id, "teams")
    token_env = _microsoft_admin_token_env(
        graph_token,
        access_token_env="AI_PLATFORM_GRAPH_ACCESS_TOKEN",
        username_token=graph_token,
    )
    if teams_token and teams_token.get("access_token") and not teams_token.get("refresh_error"):
        token_env["AI_PLATFORM_TEAMS_ACCESS_TOKEN"] = teams_token["access_token"]
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_teams_powershell",
        token_env=token_env,
        preamble=_ms_teams_powershell_preamble(),
        required_env=("AI_PLATFORM_GRAPH_ACCESS_TOKEN", "AI_PLATFORM_TEAMS_ACCESS_TOKEN"),
    )


async def run_ms_sharepoint_pnp_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute SharePoint/PnP PowerShell through the Microsoft Admin connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_sharepoint_pnp_powershell",
    )
    if error:
        return error
    site_url = str(arguments.get("site_url") or arguments.get("admin_url") or "").strip()
    if not site_url:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="ms_sharepoint_pnp_powershell",
            message="Provide site_url or admin_url for ms_sharepoint_pnp_powershell.",
            connector="ms_sharepoint_pnp_powershell",
            error_type="missing_site_url",
        )
    token = await get_microsoft_admin_token(user_id, "sharepoint", site_url=site_url)
    token_env = _microsoft_admin_token_env(
        token,
        access_token_env="AI_PLATFORM_PNP_ACCESS_TOKEN",
        username_token=token,
    )
    token_env["AI_PLATFORM_PNP_URL"] = site_url
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_sharepoint_pnp_powershell",
        token_env=token_env,
        preamble=_ms_sharepoint_pnp_powershell_preamble(),
        required_env=("AI_PLATFORM_PNP_ACCESS_TOKEN", "AI_PLATFORM_PNP_URL"),
    )


async def run_ms_az_powershell_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute Az PowerShell through the Microsoft Admin connector."""
    request_id, timeout, script, error = _prepare_microsoft_admin_powershell_script(
        arguments,
        timeout,
        connector_name="ms_az_powershell",
    )
    if error:
        return error
    token = await get_microsoft_admin_token(user_id, "arm")
    token_env = _microsoft_admin_token_env(
        token,
        access_token_env="AI_PLATFORM_ARM_ACCESS_TOKEN",
        username_token=token,
    )
    return await _run_microsoft_admin_powershell_tool(
        script,
        user_id,
        timeout,
        request_id,
        connector_name="ms_az_powershell",
        token_env=token_env,
        preamble=_ms_az_powershell_preamble(),
        required_env=("AI_PLATFORM_ARM_ACCESS_TOKEN", "AI_PLATFORM_MS_USERNAME"),
    )


async def run_ms_bicep_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute the native Bicep CLI interface for the Microsoft Admin connector."""
    request_id = uuid.uuid4().hex[:16]
    timeout = _tool_timeout(arguments, timeout)
    command = str(arguments.get("command") or "").strip()
    if not command:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="bicep",
            message="Provide command for ms_bicep.",
            connector="ms_bicep",
        )
    return await _run_microsoft_admin_bicep(
        command,
        user_id,
        timeout=timeout,
        request_id=request_id,
        connector_name="ms_bicep",
        allowed_binaries=MS_BICEP_ALLOWED_BINARIES,
    )


def _microsoft_admin_forbidden_command(script: str) -> bool:
    return bool(MS_ADMIN_FORBIDDEN_COMMAND_RE.search(script))


def _microsoft_admin_home_dir(user_id: UUID) -> str:
    base = os.environ.get("MS_ADMIN_USER_HOME_ROOT", "/tmp/ai-platform-ms-admin")
    path = os.path.join(base, user_id.hex)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _microsoft_admin_env(user_id: UUID) -> dict[str, str]:
    return {
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_CONFIG_DIR": _azure_config_dir(user_id),
        "HOME": _microsoft_admin_home_dir(user_id),
    }


def _microsoft_admin_token_env(
    token_data: Optional[dict[str, Any]],
    *,
    access_token_env: str,
    username_token: Optional[dict[str, Any]],
) -> dict[str, str]:
    env: dict[str, str] = {}
    if token_data and token_data.get("access_token") and not token_data.get("refresh_error"):
        env[access_token_env] = token_data["access_token"]
    username = extract_microsoft_admin_username(username_token or token_data or {})
    if username:
        env["AI_PLATFORM_MS_USERNAME"] = username
    return env


async def run_microsoft_pwsh_tool(
    *,
    user_id: Optional[UUID],
    tool_name: str,
    script: str,
    timeout: int,
    env: dict[str, str],
    preamble: str,
    request_id: str,
) -> dict[str, Any]:
    if not user_id:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode=tool_name,
            message="Microsoft Admin is not connected for this user.",
            command=script,
            error_type="not_connected",
            connector=tool_name,
        )

    full_script = f"{preamble}\n{script}"
    result = await run_command(
        f"pwsh -NoLogo -NoProfile -NonInteractive -Command {shlex.quote(full_script)}",
        timeout=timeout,
        env=env,
        allowed_binaries=MS_POWERSHELL_ALLOWED_BINARIES,
    )
    output = result.to_dict()
    output.update({
        "command": script,
        "connector": tool_name,
        "mode": tool_name,
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "user_scoped_microsoft_admin_shell",
    })
    if not result.success:
        output.setdefault("error_type", "command_failed")
        output.setdefault("message", _command_failure_message(output, f"{tool_name} command failed."))
    return output


def _ms_graph_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformGraph {
    if (-not $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN) { throw 'Microsoft Graph token is not available. Check Microsoft Admin tenant consent and the signed-in user''s directory roles.' }
    Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
    $secureToken = ConvertTo-SecureString $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN -AsPlainText -Force
    Connect-MgGraph -AccessToken $secureToken -NoWelcome
}
"""


def _ms_exchange_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformExchange {
    if (-not $env:AI_PLATFORM_EXCHANGE_ACCESS_TOKEN) { throw 'Exchange Online token is not available. Check Microsoft Admin tenant consent and the signed-in user''s Exchange admin roles.' }
    Import-Module ExchangeOnlineManagement -ErrorAction Stop
    Connect-ExchangeOnline -AccessToken $env:AI_PLATFORM_EXCHANGE_ACCESS_TOKEN -UserPrincipalName $env:AI_PLATFORM_MS_USERNAME -ShowBanner:$false
}
"""


def _ms_teams_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformTeams {
    if (-not $env:AI_PLATFORM_GRAPH_ACCESS_TOKEN) { throw 'Microsoft Graph token is not available. Check Microsoft Admin tenant consent and the signed-in user''s Teams/admin roles.' }
    if (-not $env:AI_PLATFORM_TEAMS_ACCESS_TOKEN) { throw 'Microsoft Teams admin token is not available. Check Microsoft Admin tenant consent and the signed-in user''s Teams/admin roles.' }
    Import-Module MicrosoftTeams -ErrorAction Stop
    Connect-MicrosoftTeams -AccessTokens @($env:AI_PLATFORM_GRAPH_ACCESS_TOKEN, $env:AI_PLATFORM_TEAMS_ACCESS_TOKEN) | Out-Null
}
"""


def _ms_sharepoint_pnp_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformPnP {
    if (-not $env:AI_PLATFORM_PNP_ACCESS_TOKEN) { throw 'SharePoint/PnP token is not available. Check Microsoft Admin tenant consent and the signed-in user''s SharePoint permissions.' }
    if (-not $env:AI_PLATFORM_PNP_URL) { throw 'SharePoint site URL is required.' }
    Import-Module PnP.PowerShell -ErrorAction Stop
    Connect-PnPOnline -Url $env:AI_PLATFORM_PNP_URL -AccessToken $env:AI_PLATFORM_PNP_ACCESS_TOKEN
}
"""


def _ms_az_powershell_preamble() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
function Connect-AIPlatformAz {
    if (-not $env:AI_PLATFORM_ARM_ACCESS_TOKEN) { throw 'Azure Resource Manager token is not available. Check Microsoft Admin tenant consent and the signed-in user''s Azure RBAC access.' }
    Import-Module Az.Accounts -ErrorAction Stop
    $secureToken = ConvertTo-SecureString $env:AI_PLATFORM_ARM_ACCESS_TOKEN -AsPlainText -Force
    Connect-AzAccount -AccessToken $secureToken -AccountId $env:AI_PLATFORM_MS_USERNAME -Tenant $env:AZURE_TENANT_ID | Out-Null
}
"""


async def _run_microsoft_admin_bicep(
    command: str,
    user_id: Optional[UUID],
    timeout: int,
    request_id: str,
    *,
    connector_name: str = "microsoft_admin",
    allowed_binaries: set[str] | None = None,
) -> dict[str, Any]:
    if _microsoft_admin_forbidden_command(command):
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="bicep",
            message="GitHub commands are not available in the Microsoft Admin connector. Use the GitHub connector.",
            command=command,
            error_type="unsupported_command",
            connector=connector_name,
        )
    normalized = command if command.startswith("bicep ") else f"bicep {command}"
    env = _microsoft_admin_env(user_id) if user_id else {}
    result = await run_command(
        normalized,
        timeout=timeout,
        env=env,
        allowed_binaries=allowed_binaries or MS_ADMIN_ALLOWED_BINARIES,
    )
    output = result.to_dict()
    output.update({
        "command": normalized,
        "connector": connector_name,
        "mode": "bicep",
        "request_id": request_id,
        "status": "success" if result.success else "failed",
        "auth_method": "local_bicep_cli",
    })
    if not result.success:
        output.setdefault("error_type", "command_failed")
        output.setdefault("message", _command_failure_message(output, "Microsoft Admin Bicep command failed."))
    return output


async def _run_microsoft_admin_graph_request(
    arguments: dict[str, Any],
    user_id: Optional[UUID],
    request_id: str,
    *,
    connector_name: str = "microsoft_admin",
) -> dict[str, Any]:
    method = str(arguments.get("method") or "GET").strip().upper()
    path = str(arguments.get("path") or "").strip()
    api_version = str(arguments.get("api_version") or "v1.0").strip().strip("/")
    max_pages = _bounded_int(arguments.get("max_pages"), GRAPH_AUTO_PAGE_MAX_PAGES, 1, 100)
    max_items = _bounded_int(arguments.get("max_items"), GRAPH_AUTO_PAGE_MAX_ITEMS, 1, 5000)
    if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="graph_request",
            message="Unsupported Graph method.",
            connector=connector_name,
        )
    if not path.startswith("/"):
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="graph_request",
            message="Graph path must start with '/'.",
            connector=connector_name,
        )

    token_data = await _get_fresh_microsoft_admin_token_for_scope(user_id, MICROSOFT_GRAPH_SCOPE) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return {
            "status": "failed",
            "connector": connector_name,
            "mode": "graph_request",
            "request_id": request_id,
            "error_type": "not_connected",
            "message": "Microsoft Graph token is not available. Check Microsoft Admin tenant consent and reconnect Microsoft Admin if the user token is expired.",
            "refresh_error": token_data.get("refresh_error") if token_data else None,
        }

    local_skip = _local_graph_skip(path)
    request_path = local_skip["path"] if local_skip else path
    fetch_max_items = max_items + int(local_skip["skip"]) if local_skip else max_items
    url = f"{MICROSOFT_GRAPH_BASE_URL.rstrip('/')}/{api_version}{request_path}"
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
            response, data = await _request_graph_with_paging(
                client,
                method,
                url,
                headers=headers,
                body=body,
                max_pages=max_pages,
                max_items=fetch_max_items,
            )
        if local_skip and response.status_code < 400:
            data = _apply_local_graph_skip(data, int(local_skip["skip"]))
        error_type, message = _graph_error_details(data, response.status_code)
        return {
            "status": "success" if response.status_code < 400 else "failed",
            "connector": connector_name,
            "mode": "graph_request",
            "request_id": request_id,
            "method": method,
            "path": path,
            "api_version": api_version,
            "status_code": response.status_code,
            "result": data,
            **({"error_type": error_type} if error_type else {}),
            **({"message": message} if message else {}),
        }
    except Exception as exc:
        logger.warning("Microsoft Graph request failed for request_id=%s: %s", request_id, exc)
        return {
            "status": "failed",
            "connector": connector_name,
            "mode": "graph_request",
            "request_id": request_id,
            "error_type": "graph_request_failed",
            "message": "Microsoft Graph request failed. Check connector logs with this request_id.",
        }


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _local_graph_skip(path: str) -> dict[str, Any] | None:
    """Handle Graph endpoints, like /users, where manual $skip is rejected.

    Microsoft Graph returns @odata.nextLink with a skip token for these
    collections. If the model still asks for $skip, fetch the collection and
    apply the requested skip locally instead of surfacing a noisy failed span.
    """
    parts = urlsplit(path)
    if parts.path.rstrip("/").lower() != "/users":
        return None

    query = parse_qsl(parts.query, keep_blank_values=True)
    skip_value: int | None = None
    kept: list[tuple[str, str]] = []
    for key, value in query:
        if key.lower() == "$skip":
            try:
                skip_value = max(0, int(value))
            except (TypeError, ValueError):
                skip_value = 0
            continue
        kept.append((key, value))

    if skip_value is None:
        return None

    cleaned = urlunsplit(("", "", parts.path, urlencode(kept, doseq=True, safe="$,()"), parts.fragment))
    return {"path": cleaned, "skip": skip_value}


async def _request_graph_with_paging(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Any,
    max_pages: int,
    max_items: int,
) -> tuple[httpx.Response, Any]:
    response = await client.request(method, url, headers=headers, json=body if body is not None else None)
    data = _graph_response_data(response)
    if method != "GET" or response.status_code >= 400 or not _is_graph_collection(data):
        return response, data

    first_data = data if isinstance(data, dict) else {}
    collected = list(first_data.get("value") or [])
    next_link = first_data.get("@odata.nextLink")
    pages = 1
    last_response = response

    while next_link and pages < max_pages and len(collected) < max_items:
        last_response = await client.request("GET", str(next_link), headers=headers)
        page_data = _graph_response_data(last_response)
        if last_response.status_code >= 400 or not _is_graph_collection(page_data):
            return last_response, page_data
        collected.extend(list(page_data.get("value") or []))
        next_link = page_data.get("@odata.nextLink")
        pages += 1

    complete = not next_link and len(collected) <= max_items
    combined = dict(first_data)
    combined["value"] = collected[:max_items]
    if not complete and next_link:
        combined["@odata.nextLink"] = next_link
    else:
        combined.pop("@odata.nextLink", None)
    combined["pagination"] = {
        "auto_paged": pages > 1,
        "pages_fetched": pages,
        "returned_count": len(combined["value"]),
        "complete": complete,
    }
    return last_response, combined


def _graph_response_data(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _is_graph_collection(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("value"), list)


def _apply_local_graph_skip(data: Any, skip: int) -> Any:
    if not _is_graph_collection(data):
        return data
    adjusted = dict(data)
    values = list(adjusted.get("value") or [])
    adjusted["value"] = values[skip:]
    pagination = dict(adjusted.get("pagination") or {})
    pagination.update({
        "local_skip_applied": skip,
        "pre_skip_count": len(values),
        "returned_count": len(adjusted["value"]),
    })
    adjusted["pagination"] = pagination
    adjusted["warning"] = (
        "The requested Microsoft Graph endpoint does not support manual $skip. "
        "The connector fetched the collection and applied the skip locally."
    )
    return adjusted


def _graph_error_details(data: Any, status_code: int) -> tuple[str | None, str | None]:
    if status_code < 400:
        return None, None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "graph_http_error")
            message = str(error.get("message") or f"Microsoft Graph returned HTTP {status_code}.")
            return code, message
    return "graph_http_error", f"Microsoft Graph returned HTTP {status_code}."


async def _microsoft_admin_status(user_id: Optional[UUID], request_id: str) -> dict[str, Any]:
    diagnosis = await diagnose_microsoft_admin_connection(user_id)
    token_data = await retrieve_token(MICROSOFT_ADMIN_PROVIDER, user_id) if user_id else None
    consented_profiles = set((token_data or {}).get("consented_scope_profiles") or [])
    primary_profile = (token_data or {}).get("scope_profile")
    if primary_profile:
        consented_profiles.add(microsoft_admin_scope_profile(primary_profile))
    return {
        **diagnosis,
        "connector": "microsoft_admin",
        "mode": "status",
        "request_id": request_id,
        "auth_profiles": {
            profile: {
                "label": microsoft_admin_scope_label(profile),
                "auth_app_name": microsoft_admin_app_name_for_scope_profile(profile),
                "client_id": microsoft_admin_client_id_for_scope_profile(profile),
                "scope_summary": microsoft_admin_scope_summary(profile),
                "consented": profile in consented_profiles,
            }
            for profile in MICROSOFT_ADMIN_SCOPE_PROFILES
        },
        "tooling": {
            "powershell_7": "pwsh",
            "graph_powershell": "Microsoft.Graph",
            "exchange_online_powershell": "ExchangeOnlineManagement",
            "teams_powershell": "MicrosoftTeams",
            "pnp_powershell": "PnP.PowerShell",
            "az_powershell": "Az",
            "azure_resource_manager_cli": "az",
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


async def _get_fresh_microsoft_admin_token(user_id: Optional[UUID]) -> Optional[dict[str, Any]]:
    if not user_id:
        return None
    token_data = await retrieve_token(MICROSOFT_ADMIN_PROVIDER, user_id)
    if not token_data:
        return None
    client_error = microsoft_admin_token_client_error(token_data)
    if client_error:
        return _invalid_microsoft_admin_token(token_data, client_error)
    expires_on = _expires_on(token_data)
    if token_data.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
        return token_data
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        return token_data
    scope_profile = microsoft_admin_scope_profile(token_data.get("scope_profile"))
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                AZURE_TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": token_data.get("client_id") or MICROSOFT_ADMIN_CLIENT_ID,
                    "refresh_token": refresh_token,
                    "scope": microsoft_admin_device_scope_string(scope_profile),
                    "client_info": "1",
                },
            )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            return {**token_data, "refresh_error": data.get("error_description") or data.get("error") or response.text[:500]}
        updated = {
            **token_data,
            "client_id": token_data.get("client_id") or MICROSOFT_ADMIN_CLIENT_ID,
            "token_type": data.get("token_type", token_data.get("token_type")),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token", refresh_token),
            "scope": data.get("scope", token_data.get("scope")),
            "scope_profile": scope_profile,
            "id_token": data.get("id_token", token_data.get("id_token")),
            "id_token_claims": _microsoft_identity_claims({"id_token": data.get("id_token")}) or token_data.get("id_token_claims"),
            "client_info": data.get("client_info", token_data.get("client_info")),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        updated["username"] = extract_microsoft_admin_username(updated)
        await store_token(MICROSOFT_ADMIN_PROVIDER, user_id, updated)
        if scope_profile == "arm":
            await ensure_azure_cli_profile(user_id, updated)
        return updated
    except Exception as exc:
        logger.warning("Microsoft Admin token refresh failed for user %s: %s", user_id.hex[:12], exc)
        return {**token_data, "refresh_error": "token_refresh_failed"}


async def _get_fresh_microsoft_admin_token_for_scope(
    user_id: Optional[UUID],
    scope: str,
    *,
    require_account_metadata: bool = False,
) -> Optional[dict[str, Any]]:
    """Return a fresh Microsoft token for a requested Microsoft Admin resource."""
    if not user_id:
        return None
    token_data = await retrieve_token(MICROSOFT_ADMIN_PROVIDER, user_id)
    if not token_data:
        return None
    client_error = microsoft_admin_token_client_error(token_data)
    if client_error:
        return _invalid_microsoft_admin_token(token_data, client_error)
    scope_profile = _scope_profile_for_scope(scope)
    if scope_profile and token_data.get("scope_profile") == scope_profile:
        expires_on = _expires_on(token_data)
        if token_data.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
            if not require_account_metadata or _has_azure_cli_account_metadata(token_data):
                return token_data
    cached_token = (token_data.get("delegated_tokens") or {}).get(scope_profile) if scope_profile else None
    cached_token_is_current = isinstance(cached_token, dict) and not microsoft_admin_token_client_error(cached_token)
    if scope_profile == "sharepoint" and cached_token_is_current:
        cached_scope = str(cached_token.get("scope") or "")
        cached_token_is_current = scope in cached_scope.split() or cached_scope == scope
    if cached_token_is_current:
        expires_on = _expires_on(cached_token)
        if cached_token.get("access_token") and (not expires_on or expires_on > int(time.time()) + 300):
            merged_token = {**token_data, **cached_token}
            if not require_account_metadata or _has_azure_cli_account_metadata(merged_token):
                return merged_token

    refresh_token = cached_token.get("refresh_token") if cached_token_is_current else None
    refresh_token = refresh_token or token_data.get("refresh_token")
    if not refresh_token:
        profile_name = microsoft_admin_scope_label(scope_profile) if scope_profile else "requested Microsoft scope"
        return _microsoft_admin_scope_unavailable(
            token_data,
            scope_profile,
            f"Stored {profile_name} token has no refresh token. Reconnect Microsoft Admin.",
            "reconnect_required",
        )
    client_id = microsoft_admin_client_id_for_scope_profile(scope_profile) if scope_profile else MICROSOFT_ADMIN_CLIENT_ID
    scope_request = _microsoft_admin_scope_request(scope, scope_profile)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                AZURE_TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "scope": scope_request,
                    "client_info": "1",
                },
            )
        data = response.json()
        if response.status_code >= 400 or "access_token" not in data:
            error_type = _microsoft_admin_oauth_error_type(data)
            return _microsoft_admin_scope_unavailable(
                token_data,
                scope_profile,
                _microsoft_admin_oauth_error_message(scope_profile, data, response.text[:500]),
                error_type,
            )
        scoped_token = {
            **token_data,
            "client_id": client_id,
            "token_type": data.get("token_type", token_data.get("token_type")),
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token", refresh_token),
            "scope": data.get("scope", scope),
            "id_token": data.get("id_token", token_data.get("id_token")),
            "id_token_claims": _microsoft_identity_claims({"id_token": data.get("id_token")}) or token_data.get("id_token_claims"),
            "client_info": data.get("client_info", token_data.get("client_info")),
            "expires_in": data.get("expires_in"),
            "expires_on": int(time.time()) + int(data.get("expires_in") or 0),
        }
        if scope_profile:
            delegated_tokens = dict(token_data.get("delegated_tokens") or {})
            delegated_tokens[scope_profile] = {
                "client_id": client_id,
                "token_type": scoped_token.get("token_type"),
                "access_token": scoped_token.get("access_token"),
                "refresh_token": scoped_token.get("refresh_token", refresh_token),
                "scope": scoped_token.get("scope"),
                "id_token": scoped_token.get("id_token"),
                "id_token_claims": scoped_token.get("id_token_claims"),
                "client_info": scoped_token.get("client_info"),
                "expires_in": scoped_token.get("expires_in"),
                "expires_on": scoped_token.get("expires_on"),
            }
            consented = set(token_data.get("consented_scope_profiles") or [])
            consented.add(scope_profile)
            await store_token(
                MICROSOFT_ADMIN_PROVIDER,
                user_id,
                {
                    **token_data,
                    "delegated_tokens": delegated_tokens,
                    "consented_scope_profiles": sorted(consented),
                },
            )
        return scoped_token
    except Exception as exc:
        logger.warning("Microsoft scoped token refresh failed for user %s scope=%s: %s", user_id.hex[:12], scope, exc)
        return _microsoft_admin_scope_unavailable(
            token_data,
            scope_profile,
            "Microsoft Admin scoped token refresh failed. Check connector logs.",
            "token_refresh_failed",
        )


async def warm_microsoft_admin_delegated_tokens(user_id: Optional[UUID]) -> dict[str, Any]:
    """Best-effort silent token warmup for secondary Microsoft Admin resources."""
    if not user_id:
        return {}
    results: dict[str, Any] = {}
    for profile, scope in (
        ("exchange", EXCHANGE_ONLINE_SCOPE),
        ("arm", AZURE_ARM_SCOPE),
        ("teams", TEAMS_TENANT_ADMIN_SCOPE),
    ):
        token = await _get_fresh_microsoft_admin_token_for_scope(user_id, scope)
        results[profile] = {
            "status": "available" if token and token.get("access_token") and not token.get("refresh_error") else "missing",
            "message": token.get("refresh_error") if token else "No token returned.",
        }
    return results


def _scope_profile_for_scope(scope: str) -> str:
    for profile, configured_scope in MICROSOFT_ADMIN_SCOPE_PROFILES.items():
        configured_values = list(configured_scope)
        if scope in configured_values or " ".join(scope.split()) == " ".join(configured_values):
            return profile
    parsed = urlsplit(scope)
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith(".sharepoint.com"):
        if parsed.path.rstrip("/").endswith(".default"):
            return "sharepoint"
    return ""


def _microsoft_admin_scope_request(scope: str, scope_profile: str | None) -> str:
    if scope_profile == "sharepoint":
        return f"{scope} openid profile offline_access"
    return microsoft_admin_device_scope_string(scope_profile) if scope_profile else f"{scope} openid profile offline_access"


def _sharepoint_scope_for_url(site_url: str | None) -> str:
    parsed = urlsplit(str(site_url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    return f"https://{parsed.hostname}/.default"


async def get_microsoft_admin_token(
    user_id: Optional[UUID],
    profile: str,
    **context: Any,
) -> Optional[dict[str, Any]]:
    """Return a fresh delegated Microsoft Admin token for one authorization profile."""
    scope_profile = microsoft_admin_scope_profile(profile)
    if scope_profile == "arm":
        scope = AZURE_ARM_SCOPE
    elif scope_profile == "exchange":
        scope = EXCHANGE_ONLINE_SCOPE
    elif scope_profile == "teams":
        scope = TEAMS_TENANT_ADMIN_SCOPE
    elif scope_profile == "sharepoint":
        scope = _sharepoint_scope_for_url(context.get("site_url") or context.get("admin_url"))
        if not scope:
            return None
    else:
        scope = MICROSOFT_GRAPH_SCOPE
    return await _get_fresh_microsoft_admin_token_for_scope(
        user_id,
        scope,
        require_account_metadata=bool(context.get("require_account_metadata")),
    )


def _expires_on(token_data: dict[str, Any]) -> int:
    try:
        return int(token_data.get("expires_on") or 0)
    except (TypeError, ValueError):
        return 0


def _token_expired(token_data: dict[str, Any]) -> bool:
    expires_on = _expires_on(token_data)
    return bool(expires_on and expires_on <= int(time.time()))


def extract_microsoft_admin_username(token_data: dict[str, Any]) -> str:
    for claims in _microsoft_claim_sets(token_data):
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

    for claims in _microsoft_claim_sets(token_data):
        for key in ("oid", "sub"):
            value = claims.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _microsoft_identity_claims(token_data: dict[str, Any]) -> dict[str, Any]:
    claims = token_data.get("id_token_claims")
    if isinstance(claims, dict):
        return claims
    return _decode_jwt_claims(token_data.get("id_token", ""))


def _microsoft_claim_sets(token_data: dict[str, Any]) -> list[dict[str, Any]]:
    claim_sets = []
    id_claims = _microsoft_identity_claims(token_data)
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
    response = {
        "token_type": token_data.get("token_type") or "Bearer",
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "id_token": token_data.get("id_token"),
        "id_token_claims": _microsoft_identity_claims(token_data),
        "client_info": client_info,
        "scope": token_data.get("scope") or microsoft_admin_arm_device_scope_string(),
        "expires_in": int(token_data.get("expires_in") or max(_expires_on(token_data) - int(time.time()), 0) or 3600),
    }
    response = {key: value for key, value in response.items() if value}
    event = {
        "client_id": token_data.get("client_id") or MICROSOFT_ADMIN_CLIENT_ID,
        "scope": (token_data.get("scope") or microsoft_admin_arm_device_scope_string()).split(),
        "token_endpoint": AZURE_TOKEN_ENDPOINT,
        "environment": "login.microsoftonline.com",
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "response": response,
        "data": {"username": token_data.get("username") or extract_microsoft_admin_username(token_data)},
    }
    cache.add(event)
    _atomic_write(cache_path, cache.serialize(), mode=0o600)


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


async def diagnose_microsoft_admin_connection(user_id: Optional[UUID]) -> dict[str, Any]:
    request_id = uuid.uuid4().hex[:16]
    token_data = await _get_fresh_microsoft_admin_token(user_id) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return {
            "status": "failed",
            "connector": "microsoft_admin",
            "request_id": request_id,
            "message": "Microsoft Admin is not connected for this user.",
        }
    if _token_expired(token_data):
        return {
            "status": "failed",
            "connector": "microsoft_admin",
            "request_id": request_id,
            "message": "Microsoft Admin token is expired. Reconnect Microsoft Admin for this user.",
        }

    try:
        graph_token = await _get_fresh_microsoft_admin_token_for_scope(user_id, MICROSOFT_GRAPH_SCOPE)
        if not graph_token or not graph_token.get("access_token") or graph_token.get("refresh_error"):
            return {
                "status": "failed",
                "connector": "microsoft_admin",
                "request_id": request_id,
                "message": (
                    graph_token.get("refresh_error")
                    if graph_token
                    else "Microsoft Graph token is not available. Reconnect Microsoft Admin."
                ),
                "graph_status": "failed",
            }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{MICROSOFT_GRAPH_BASE_URL.rstrip('/')}/v1.0/me?$select=id,displayName,userPrincipalName,mail",
                headers={"Authorization": f"Bearer {graph_token['access_token']}"},
            )
        graph_data = _graph_response_data(response)
        error_type, graph_message = _graph_error_details(graph_data, response.status_code)
        if response.status_code >= 400:
            return {
                "status": "failed",
                "connector": "microsoft_admin",
                "request_id": request_id,
                "message": graph_message or "Microsoft Graph validation failed.",
                "error_type": error_type or "graph_validation_failed",
                "graph_status": "failed",
                "status_code": response.status_code,
            }

        secondary = await warm_microsoft_admin_delegated_tokens(user_id)
        arm_details: dict[str, Any] = {}
        arm_token = await _get_fresh_microsoft_admin_token_for_scope(user_id, AZURE_ARM_SCOPE)
        if arm_token and arm_token.get("access_token") and not arm_token.get("refresh_error"):
            subscriptions_result = await _list_azure_subscriptions(arm_token["access_token"])
            if subscriptions_result.get("ok"):
                subscriptions = subscriptions_result.get("subscriptions", [])
                arm_details = {
                    "status": "available",
                    "subscriptions_count": len(subscriptions),
                    "subscriptions": [
                        {
                            "subscription_id": sub.get("subscriptionId"),
                            "display_name": sub.get("displayName"),
                            "state": sub.get("state"),
                        }
                        for sub in subscriptions[:10]
                    ],
                }
            else:
                arm_details = {
                    "status": "limited",
                    "message": subscriptions_result.get("message"),
                    "stderr": subscriptions_result.get("stderr", ""),
                }
        else:
            arm_details = {
                "status": "missing",
                "message": arm_token.get("refresh_error") if arm_token else "Azure Resource Manager token is not available.",
            }

        return {
            "status": "success",
            "connector": "microsoft_admin",
            "request_id": request_id,
            "message": "Microsoft Admin is connected. Microsoft Graph validation succeeded.",
            "graph_status": "available",
            "graph_user": graph_data if isinstance(graph_data, dict) else {},
            "authorization_profiles": {
                "graph": {"status": "available", "label": microsoft_admin_scope_label("graph")},
                "exchange": {"label": microsoft_admin_scope_label("exchange"), **secondary.get("exchange", {})},
                "arm": {"label": microsoft_admin_scope_label("arm"), **arm_details},
                "teams": {"label": microsoft_admin_scope_label("teams"), **secondary.get("teams", {})},
                "sharepoint": {
                    "status": "not_checked",
                    "label": microsoft_admin_scope_label("sharepoint"),
                    "message": "SharePoint/PnP tokens are target-site scoped and are acquired when ms_sharepoint_pnp_powershell is run with a site_url or admin_url.",
                },
            },
        }
    except Exception as exc:
        logger.warning("Microsoft Admin diagnostics failed for request_id=%s: %s", request_id, exc)
        return {
            "status": "failed",
            "connector": "microsoft_admin",
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
