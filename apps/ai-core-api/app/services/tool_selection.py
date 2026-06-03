"""Expose consolidated tools for each system the current user has connected."""
import json
import logging
from dataclasses import dataclass, field
from uuid import UUID
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import AITool, AIConnectedAccount

logger = logging.getLogger(__name__)

CONSOLIDATED_TOOL_NAMES = {"odoo_ops_runner", "azure_cli", "github_cli"}


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
    _user_message: str,
    _task_type: str = "general_chat",
    _risk_level: str = "low",
    connected_systems: Optional[set[str]] = None,
) -> ToolSelectionResult:
    """Select all consolidated tools for connected systems."""
    result = ToolSelectionResult()

    if connected_systems is None:
        acct_result = await db.execute(
            select(AIConnectedAccount).where(
                AIConnectedAccount.user_id == user_id,
                AIConnectedAccount.status.in_(("connected", "active")),
            )
        )
        connected_systems = {a.provider for a in acct_result.scalars().all()}
    if not connected_systems:
        return result

    tool_result = await db.execute(
        select(AITool).where(
            AITool.status == "active",
            AITool.target_system.in_(connected_systems),
        ).order_by(AITool.name)
    )
    all_tools: list[AITool] = tool_result.scalars().all()
    if not all_tools:
        return result

    result.schema_size_before = _schema_size(all_tools)

    selected = [t for t in all_tools if t.name in CONSOLIDATED_TOOL_NAMES]

    result.selected = selected
    result.excluded = [t for t in all_tools if t.name not in CONSOLIDATED_TOOL_NAMES]
    result.selection_reason = "all_connected_consolidated_tools"
    result.schema_size_after = _schema_size(result.selected)

    logger.info(
        "Tool selection | intent=%s total=%d selected=%d excluded=%d schema_before=%d schema_after=%d reason=%s",
        result.intent, len(all_tools), len(result.selected), len(result.excluded),
        result.schema_size_before, result.schema_size_after, result.selection_reason,
    )

    return result
