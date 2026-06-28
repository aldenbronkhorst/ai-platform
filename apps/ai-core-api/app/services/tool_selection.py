"""Expose active tools for systems the current user has connected."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AITool
from app.services.connected_account_state import effective_connected_accounts
from app.services.tool_registry import CONSOLIDATED_TOOL_NAMES, is_model_facing_tool

logger = logging.getLogger(__name__)

DOCUMENT_READER_TOOL = "document_reader"
WORKSPACE_TOOL = "workspace"
DOCUMENT_CONTEXT_MARKERS = {
    "[attached file context]",
    "attached file",
    "attachment",
    "uploaded",
    "document",
    "pdf",
    "read the file",
}


@dataclass
class ToolSelectionResult:
    selected: list[AITool] = field(default_factory=list)
    excluded: list[AITool] = field(default_factory=list)
    intent: str = "connected_tools"
    selection_reason: str = ""
    schema_size_before: int = 0
    schema_size_after: int = 0


def _schema_size(tools: list[AITool]) -> int:
    return sum(len(json.dumps(tool.input_schema or {})) for tool in tools)


def _needs_document_reader(user_message: str, task_type: str) -> bool:
    message = (user_message or "").lower()
    return task_type in {"document", "documents", "attachment"} or any(
        marker in message for marker in DOCUMENT_CONTEXT_MARKERS
    )


async def get_tool_selection(
    db: AsyncSession,
    user_id: UUID,
    _user_message: str,
    _task_type: str = "general_chat",
    _risk_level: str = "low",
    connected_systems: Optional[set[str]] = None,
) -> ToolSelectionResult:
    """Return active model-facing tools for connected systems.

    Tool choice belongs to the model. This selector only enforces what the user
    has connected and what the platform exposes as model-facing.
    """
    result = ToolSelectionResult()

    if connected_systems is None:
        accounts = await effective_connected_accounts(db, user_id)
        connected_systems = {a.provider for a in accounts if a.status in ("connected", "active")}

    requested_platform_tools = {WORKSPACE_TOOL}
    if _needs_document_reader(_user_message, _task_type):
        requested_platform_tools.add(DOCUMENT_READER_TOOL)
    intent_parts = sorted(connected_systems)
    if requested_platform_tools:
        intent_parts.append("ai-platform")
    result.intent = ",".join(intent_parts) if intent_parts else "no_connected_tools"

    tool_filters = []
    if connected_systems:
        tool_filters.append(
            and_(
                AITool.target_system.in_(connected_systems),
                AITool.name.in_(CONSOLIDATED_TOOL_NAMES),
            )
        )
    if requested_platform_tools:
        tool_filters.append(AITool.name.in_(requested_platform_tools))
    if not tool_filters:
        return result

    tool_result = await db.execute(
        select(AITool).where(AITool.status == "active", or_(*tool_filters)).order_by(AITool.name)
    )
    tools = [
        tool
        for tool in tool_result.scalars().all()
        if tool.status in (None, "active")
        and is_model_facing_tool(tool.name, tool.target_system)
        and (
            (tool.target_system in connected_systems and tool.name in CONSOLIDATED_TOOL_NAMES)
            or tool.name in requested_platform_tools
        )
    ]

    result.selected = tools
    result.schema_size_before = _schema_size(tools)
    result.schema_size_after = result.schema_size_before
    result.selection_reason = "connected_tools_available" if tools else "no_active_connected_tools"

    logger.info(
        "Tool selection | intent=%s selected=%d schema=%d reason=%s",
        result.intent,
        len(result.selected),
        result.schema_size_after,
        result.selection_reason,
    )
    return result
