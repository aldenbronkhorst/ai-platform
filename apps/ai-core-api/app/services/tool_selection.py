"""Generic dynamic tool exposure service for all connectors.

Exposes all consolidated tools for systems the current user has connected.
Intent is still classified for observability, but it no longer removes tools
from the request.
"""
import logging
import re
from typing import Any, Optional
from uuid import UUID
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import AITool, AIConnectedAccount

logger = logging.getLogger(__name__)

# Intent classifiers per connector
INTENT_PATTERNS: dict[str, list[re.Pattern]] = {
    "odoo_lookup": [
        re.compile(r"(check|search|find|look\s+up|see|locate)\s+.*odoo", re.IGNORECASE),
        re.compile(r"(credit note|credit_notes|refund|invoice|bill|receipt|partner|vendor|customer|product|order|sale)", re.IGNORECASE),
        re.compile(r"(attachment|pdf|file|document)s?\s*(on|for|attached|of)", re.IGNORECASE),
    ],
    "odoo_report": [
        re.compile(r"(report|p&l|pnl|profit.*loss|balance sheet|trial balance|aged|ledger|tax report)", re.IGNORECASE),
        re.compile(r"(revenue|income|expense|gross|net).*(this|last|current).*(month|year|quarter)", re.IGNORECASE),
    ],
    "odoo_mutation": [
        re.compile(r"(create|write|update|change|modify|delete|remove|cancel|archive)\s+(odoo|sale|invoice|bill|order|partner)", re.IGNORECASE),
        re.compile(r"(confirm|approve|validate|done)\s+(sale|order|invoice|bill)", re.IGNORECASE),
    ],
    "odoo_chatter": [
        re.compile(r"(chatter|message|note|comment|discuss|conversation)\s+(on|for|of)", re.IGNORECASE),
        re.compile(r"post\s+(a\s+)?(note|message|comment)\s+(on|to)", re.IGNORECASE),
    ],
    "azure_infra": [
        re.compile(r"(azure|az cli|containerapp|aks|key.vault|service.bus|storage.account|resource.group|cognitive)", re.IGNORECASE),
        re.compile(r"(deployment|revision|log|metric|quota)\s*(azure|foundry|cognitive)", re.IGNORECASE),
    ],
    "github_dev": [
        re.compile(r"(github|gh|git|repo|commit|pr|pull.request|issue|action|workflow)", re.IGNORECASE),
        re.compile(r"(run|build|test|deploy|ci/cd|pipeline)\s*(github|action)", re.IGNORECASE),
    ],
    "deployment_debug": [
        re.compile(r"(deployment|release|rollback|incident|outage)\s+(failed|stuck|broken)", re.IGNORECASE),
        re.compile(r"(why did|what caused|check|investigate)\s+(deploy|build|release)", re.IGNORECASE),
    ],
}

class ToolSelectionResult:
    def __init__(self):
        self.selected: list[AITool] = []
        self.excluded: list[AITool] = []
        self.intent: str = "general"
        self.selection_reason: str = ""
        self.schema_size_before: int = 0
        self.schema_size_after: int = 0
        self.guidance_tokens: int = 0


async def get_tool_selection(
    db: AsyncSession,
    user_id: UUID,
    user_message: str,
    task_type: str = "general_chat",
    risk_level: str = "low",
) -> ToolSelectionResult:
    """Select all consolidated tools for connected systems."""
    result = ToolSelectionResult()

    # Get all available tools for connected systems
    acct_result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
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

    # Calculate total schema size for observability
    import json
    result.schema_size_before = sum(len(json.dumps(t.input_schema or {})) for t in all_tools)

    # Classify intent
    intent = _classify_intent(user_message, task_type, risk_level)
    result.intent = intent

    # Exclude deprecated/legacy fragmented tools; keep only consolidated ones
    consolidated_tool_names = {"odoo_ops_runner", "azure_cli", "github_cli"}
    non_deprecated = [t for t in all_tools if t.name in consolidated_tool_names]

    result.selected = non_deprecated
    result.excluded = []
    result.selection_reason = "all_connected_consolidated_tools"

    # Calculate after size
    result.schema_size_after = sum(len(json.dumps(t.input_schema or {})) for t in result.selected)

    logger.info(
        "Tool selection | intent=%s total=%d selected=%d excluded=%d schema_before=%d schema_after=%d reason=%s",
        intent, len(all_tools), len(result.selected), len(result.excluded),
        result.schema_size_before, result.schema_size_after, result.selection_reason,
    )

    return result


def _classify_intent(message: str, task_type: str, risk_level: str) -> str:
    if not message:
        return "general"
    q = message.lower()

    # Check each connector's patterns
    for intent, patterns in INTENT_PATTERNS.items():
        for pat in patterns:
            if pat.search(q):
                return intent

    # High-risk finance → full report tools
    if risk_level == "high" and task_type == "general_chat":
        finance_kw = ["revenue", "income", "expense", "profit", "loss", "balance",
                      "invoice", "bill", "payment", "cost", "price", "tax", "vat", "accounting"]
        if any(kw in q for kw in finance_kw):
            return "odoo_report"

    return "general"
