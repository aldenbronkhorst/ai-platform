"""Bicep CLI execution for the native Azure CLI connector."""
from __future__ import annotations

import uuid
from typing import Any, Optional
from uuid import UUID

from app.services.ops_command_runner import run_command
from app.services.connectors.microsoft_admin.constants import MS_BICEP_ALLOWED_BINARIES
from app.services.connectors.microsoft_admin.powershell_common import (
    _command_failure_message,
    _failed_microsoft_admin_result,
    _tool_timeout,
)

async def run_ms_bicep_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute the native Bicep CLI interface for the Azure CLI connector."""
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
    connector_name: str = "ms_bicep",
    allowed_binaries: set[str] | None = None,
) -> dict[str, Any]:
    _ = user_id
    normalized = command if command.startswith("bicep ") else f"bicep {command}"
    result = await run_command(
        normalized,
        timeout=timeout,
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
        output.setdefault("message", _command_failure_message(output, "Bicep command failed."))
    return output
