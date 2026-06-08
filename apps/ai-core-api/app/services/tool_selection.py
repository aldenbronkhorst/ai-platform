"""Expose consolidated tools for each system the current user has connected."""
import json
import logging
import re
from dataclasses import dataclass, field
from uuid import UUID
from typing import Optional
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import AITool
from app.services.connected_account_state import effective_connected_accounts
from app.services.tool_registry import CONNECTOR_TOOL_BY_SYSTEM, CONSOLIDATED_TOOL_NAMES, is_model_facing_tool

logger = logging.getLogger(__name__)

TOOL_BY_SYSTEM = dict(CONNECTOR_TOOL_BY_SYSTEM)
DOCUMENT_READER_TOOL = "document_reader"
PLATFORM_TOOL_NAMES = frozenset({DOCUMENT_READER_TOOL})
SYSTEM_INTENT_KEYWORDS = {
    "odoo": {
        "odoo", "invoice", "invoices", "bill", "bills", "credit note", "refund",
        "customer", "customers", "supplier", "suppliers", "partner", "partners",
        "sales order", "purchase order", "quotation", "product", "products",
        "stock", "inventory", "delivery", "accounting", "journal", "ledger",
        "balance sheet", "trial balance", "p&l", "pnl", "profit and loss",
        "turnover", "revenue", "income", "sales", "profit", "loss",
        "expense", "expenses", "payment", "payments",
        "receipt", "receipts", "crm",
    },
    "azure": {
        "azure", "az", "resource group", "resource groups", "subscription",
        "subscriptions", "tenant", "container app", "container apps", "revision",
        "revisions", "key vault", "storage",
        "blob", "service bus", "queue", "queues", "azure search", "foundry",
        "apim", "api management", "managed identity",
        "rbac", "role assignment", "vnet", "network", "dns", "keda",
        "bicep", "microsoft admin", "m365", "microsoft 365", "office 365",
        "entra", "entra id", "graph", "microsoft graph", "powershell", "pwsh",
        "exchange", "exchange online", "mailbox", "mailboxes", "mail flow",
        "transport rule", "message trace", "intune", "device management",
        "managed device", "compliance policy", "conditional access",
        "teams admin", "sharepoint admin", "connect-mggraph", "connect-exchangeonline",
        "connect-microsoftteams", "connect-azaccount",
    },
    "github": {
        "github", "gh", "git", "repo", "repos", "repository", "repositories",
        "branch", "branches", "commit", "commits", "pull request", "pull requests",
        "pr", "prs", "issue", "issues", "workflow", "workflows", "github actions",
        "action run", "ci", "release", "releases", "tag", "tags", "deploy key",
        "code search",
    },
}
DOCUMENT_INTENT_KEYWORDS = {
    "attached file", "attached files", "attachment", "attachments", "uploaded",
    "upload", "document", "documents", "pdf", "ocr", "scan", "scanned",
    "extract text", "read the file", "read this file", "summarize the file",
}
SHORT_KEYWORDS = {"az", "gh", "git", "pr", "prs", "pnl"}
BROAD_CONNECTED_PATTERNS = {
    "all connected systems", "all connected accounts", "all connectors",
    "connected systems", "connected accounts", "available connectors",
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


def _message_tokens(message: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_&+-]+", message.lower()))


def _contains_keyword(message: str, tokens: set[str], keyword: str) -> bool:
    keyword = keyword.lower()
    if keyword in SHORT_KEYWORDS:
        return keyword in tokens
    return keyword in message


def _requested_systems(user_message: str, task_type: str) -> set[str]:
    message = (user_message or "").lower()
    tokens = _message_tokens(message)
    if any(pattern in message for pattern in BROAD_CONNECTED_PATTERNS):
        return set(TOOL_BY_SYSTEM)

    requested: set[str] = set()
    for system, keywords in SYSTEM_INTENT_KEYWORDS.items():
        if any(_contains_keyword(message, tokens, keyword) for keyword in keywords):
            requested.add(system)

    if task_type in {"azure", "github", "odoo"}:
        requested.add(task_type)
    return requested


def _requested_platform_tools(user_message: str, task_type: str) -> set[str]:
    message = (user_message or "").lower()
    if "[attached file context]" in message or any(keyword in message for keyword in DOCUMENT_INTENT_KEYWORDS):
        return {DOCUMENT_READER_TOOL}
    if task_type in {"document", "documents", "attachment"}:
        return {DOCUMENT_READER_TOOL}
    return set()


async def get_tool_selection(
    db: AsyncSession,
    user_id: UUID,
    _user_message: str,
    _task_type: str = "general_chat",
    _risk_level: str = "low",
    connected_systems: Optional[set[str]] = None,
) -> ToolSelectionResult:
    """Select consolidated tools only when the current message points at that system."""
    result = ToolSelectionResult()

    if connected_systems is None:
        accounts = await effective_connected_accounts(db, user_id)
        connected_systems = {a.provider for a in accounts if a.status in ("connected", "active")}

    requested_systems = _requested_systems(_user_message, _task_type)
    requested_platform_tools = _requested_platform_tools(_user_message, _task_type)
    eligible_systems = connected_systems.intersection(requested_systems)
    intent_parts = sorted(eligible_systems)
    if requested_platform_tools:
        intent_parts.append("ai-platform")
    result.intent = ",".join(intent_parts) if intent_parts else "no_connector_intent"

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
        select(AITool).where(
            AITool.status == "active",
            or_(*tool_filters),
        ).order_by(AITool.name)
    )
    all_tools = [
        tool
        for tool in tool_result.scalars().all()
        if is_model_facing_tool(tool.name, tool.target_system)
        and (
            (tool.target_system in connected_systems and tool.name in CONSOLIDATED_TOOL_NAMES)
            or tool.name in requested_platform_tools
        )
    ]
    if not all_tools:
        return result

    result.schema_size_before = _schema_size(all_tools)

    selected_tool_names = {TOOL_BY_SYSTEM[system] for system in eligible_systems if system in TOOL_BY_SYSTEM}
    selected_tool_names.update(requested_platform_tools)
    selected = [t for t in all_tools if t.name in selected_tool_names]

    result.selected = selected
    result.excluded = [t for t in all_tools if t.name not in selected_tool_names]
    if selected:
        result.selection_reason = (
            "message_intent_matched_available_tools"
            if requested_platform_tools
            else "message_intent_matched_connected_systems"
        )
    else:
        result.selection_reason = "no_matching_connector_intent"
    result.schema_size_after = _schema_size(result.selected)

    logger.info(
        "Tool selection | intent=%s total=%d selected=%d excluded=%d schema_before=%d schema_after=%d reason=%s",
        result.intent, len(all_tools), len(result.selected), len(result.excluded),
        result.schema_size_before, result.schema_size_after, result.selection_reason,
    )

    return result
