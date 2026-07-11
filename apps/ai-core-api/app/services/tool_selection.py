"""Expose a compact model-facing tool surface."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AITool
from app.services.connected_account_state import effective_connected_accounts
from app.services.tool_registry import is_model_facing_tool

logger = logging.getLogger(__name__)


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


async def get_tool_selection(
    db: AsyncSession,
    user_id: UUID,
    user_message: str,
    _task_type: str = "general_chat",
    _risk_level: str = "low",
    connected_systems: Optional[set[str]] = None,
) -> ToolSelectionResult:
    """Return active model-facing tools for the user's workspace.

    Connected-system credentials are available inside Workspace through the
    broker. The model-facing surface stays intentionally small.
    """
    result = ToolSelectionResult()

    if connected_systems is None:
        accounts = await effective_connected_accounts(db, user_id)
        connected_systems = {a.provider for a in accounts if a.status in ("connected", "active")}

    tool_result = await db.execute(
        select(AITool).where(AITool.status == "active").order_by(AITool.name)
    )
    tools = [
        tool
        for tool in tool_result.scalars().all()
        if tool.status in (None, "active")
        and is_model_facing_tool(tool.name, tool.target_system)
    ]

    result.selected = tools
    intent_parts = sorted(connected_systems)
    if tools:
        intent_parts.append("ai-platform")
    result.intent = ",".join(intent_parts) if intent_parts else "ai-platform"
    result.schema_size_before = _schema_size(tools)
    result.schema_size_after = result.schema_size_before
    result.selection_reason = "model_facing_tools_available" if tools else "no_active_model_facing_tools"

    logger.info(
        "Tool selection | intent=%s selected=%d schema=%d reason=%s",
        result.intent,
        len(result.selected),
        result.schema_size_after,
        result.selection_reason,
    )
    return result
