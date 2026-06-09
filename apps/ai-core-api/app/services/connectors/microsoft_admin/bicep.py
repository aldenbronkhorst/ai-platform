"""Bicep CLI execution for the Microsoft Admin connector."""
from __future__ import annotations

import uuid
from typing import Any, Optional
from uuid import UUID

from app.services.ops_command_runner import run_command
from app.services.connectors.microsoft_admin.constants import MS_BICEP_ALLOWED_BINARIES
from app.services.connectors.microsoft_admin.powershell_common import (
    _command_failure_message,
    _failed_microsoft_admin_result,
    _microsoft_admin_env,
    _microsoft_admin_forbidden_command,
    _tool_timeout,
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
        allowed_binaries=allowed_binaries or MS_BICEP_ALLOWED_BINARIES,
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
