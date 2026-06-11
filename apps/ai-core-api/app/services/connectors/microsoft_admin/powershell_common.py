"""Shared PowerShell and result helpers for native Microsoft tools."""
from __future__ import annotations

import os
import shlex
import uuid
from typing import Any, Optional
from uuid import UUID

from app.services.ops_command_runner import run_command
from app.services.connectors.microsoft_admin.constants import (
    MS_ADMIN_FORBIDDEN_COMMAND_RE,
    MS_POWERSHELL_ALLOWED_BINARIES,
    TENANT_ID,
)
from app.services.connectors.microsoft_admin.tokens import extract_microsoft_admin_username

def _tool_timeout(arguments: dict[str, Any], default: int = 60) -> int:
    try:
        timeout_value = int(arguments.get("timeout") or default or 60)
    except (TypeError, ValueError):
        timeout_value = 60
    return max(1, min(timeout_value, 300))

def _failed_microsoft_admin_result(
    *,
    request_id: str,
    mode: str,
    message: str,
    command: str = "",
    error_type: str = "invalid_tool_arguments",
    connector: str = "microsoft_native",
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
            message="GitHub commands are not available in Microsoft tool connectors. Use the GitHub connector.",
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
            message=f"{connector_name} is not connected for this user.",
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
                f"{connector_name} token is not available. "
                "Reconnect that Microsoft connector and ensure the signed-in user has the required workload permissions."
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

def _microsoft_admin_forbidden_command(script: str) -> bool:
    return bool(MS_ADMIN_FORBIDDEN_COMMAND_RE.search(script))


def _microsoft_admin_home_dir(user_id: UUID) -> str:
    base = os.environ.get("MS_NATIVE_USER_HOME_ROOT", os.environ.get("MS_ADMIN_USER_HOME_ROOT", "/tmp/ai-platform-ms-native"))
    path = os.path.join(base, user_id.hex)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _microsoft_admin_env(user_id: UUID) -> dict[str, str]:
    from app.services.connectors.microsoft_admin.azure_cli import _azure_config_dir

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
            message=f"{tool_name} is not connected for this user.",
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
        "auth_method": "native_microsoft_tool_shell",
    })
    if not result.success:
        output.setdefault("error_type", "command_failed")
        output.setdefault("message", _command_failure_message(output, f"{tool_name} command failed."))
    return output
