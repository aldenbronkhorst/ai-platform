import os
import re
import json
import logging
from calendar import monthrange
from uuid import UUID
from datetime import datetime, timedelta
from typing import Optional, Any
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
import httpx

from app.models.models import (
    AIProvider, AIModel, AIRoute, AIUsageLog, AIConnectedAccount, AITool, AICompanyFact,
    AIMemory,
)
from app.services.foundry_client import FoundryClient
from app.services.context import ContextService
from app.schemas.schemas import ContextRequest

logger = logging.getLogger(__name__)

ROUTE_NOT_CONFIGURED_MESSAGE = "AI chat is not configured yet. Please ask an administrator to configure a model in Settings \u2192 AI Configuration."

CANONICAL_SYSTEM_PROMPT = (
    "You are the AI Platform for Lots Lots More. "
    "You help employees work across company knowledge, workflows, documents, "
    "tasks, connected accounts, and business systems. "
    "You are not tied to one system. "
    "You may use connected tools such as Odoo, GitHub, Azure, Microsoft 365, "
    "documents, and future connectors only when they are available, authorised, "
    "and relevant. "
    "Never claim live access to a system unless that connector is connected and "
    "permitted for the current user. "
    "If a required connector is not connected, explain that clearly and guide "
    "the user to Connected Accounts. "
    "Keep responses practical, business-focused, and clear."
)


class RouteNotFoundError(Exception):
    def __init__(self, task_type: str):
        self.task_type = task_type
        super().__init__(ROUTE_NOT_CONFIGURED_MESSAGE)


class ProviderCallError(Exception):
    def __init__(self, message: str, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(message)


async def get_enabled_route(db: AsyncSession, task_type: str = "general_chat") -> tuple:
    result = await db.execute(
        select(AIRoute).where(AIRoute.task_type == task_type, AIRoute.enabled == "true")
    )
    route = result.scalar_one_or_none()
    if not route:
        raise RouteNotFoundError(task_type)

    model_result = await db.execute(
        select(AIModel).where(AIModel.id == route.primary_model_id, AIModel.enabled == "true")
    )
    model = model_result.scalar_one_or_none()
    if not model:
        raise RouteNotFoundError(task_type)

    provider_result = await db.execute(
        select(AIProvider).where(AIProvider.id == model.provider_id, AIProvider.enabled == "true")
    )
    provider = provider_result.scalar_one_or_none()
    if not provider:
        raise RouteNotFoundError(task_type)

    return route, model, provider


_secret_client: Optional[SecretClient] = None


def _get_kv_client() -> SecretClient:
    global _secret_client
    if _secret_client is None:
        vault_uri = os.environ.get("KEY_VAULT_URI", "")
        if vault_uri:
            credential = DefaultAzureCredential()
            _secret_client = SecretClient(vault_url=vault_uri, credential=credential)
    return _secret_client


async def _resolve_api_key(provider: AIProvider) -> Optional[str]:
    """Try Key Vault secret first, then env var, then fall back to hard-coded."""
    if provider.auth_type == "key_vault_secret" and provider.secret_reference:
        try:
            client = _get_kv_client()
            if client:
                secret = client.get_secret(provider.secret_reference)
                if secret and secret.value:
                    return secret.value
        except Exception as exc:
            logger.warning("Failed to fetch KV secret %s: %s", provider.secret_reference, exc)
    # Fallback: check environment variable
    env_key = provider.name.upper().replace(" ", "_") + "_API_KEY"
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val
    return None


async def build_foundry_client(provider: AIProvider, model: AIModel) -> FoundryClient:
    api_key = await _resolve_api_key(provider)
    use_mi = provider.auth_type == "managed_identity"
    return FoundryClient(
        base_url=provider.base_url,
        deployment_name=model.deployment_name,
        api_key=api_key,
        use_managed_identity=use_mi and not api_key,
    )


KNOWN_CONNECTOR_TYPES = ["odoo", "github", "azure", "microsoft_365", "azure_devops"]

CONNECTOR_DISPLAY_NAMES: dict[str, str] = {
    "odoo": "Odoo",
    "github": "GitHub",
    "azure": "Azure",
    "microsoft_365": "Microsoft 365",
    "azure_devops": "Azure DevOps",
    "slack": "Slack",
    "teams": "Microsoft Teams",
}

TOOL_CONNECTOR_MAP = {
    "odoo": {
        "connector_url_env": "ODOO_CONNECTOR_URL",
        "connector_key_env": "ODOO_CONNECTOR_API_KEY",
    },
}

ODOO_CONNECTOR_URL: str = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY: str = os.environ.get("ODOO_CONNECTOR_API_KEY", "")


async def _get_available_tools(db: AsyncSession, user_id: Optional[UUID]) -> list[AITool]:
    """Get active tools for systems the user has connected accounts for."""
    if not user_id:
        return []
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
        )
    )
    accounts = result.scalars().all()
    connected_systems = {a.provider for a in accounts}
    if not connected_systems:
        return []
    result = await db.execute(
        select(AITool).where(
            AITool.status == "active",
            AITool.target_system.in_(connected_systems),
        ).order_by(AITool.name)
    )
    return result.scalars().all()


def _normalize_tool_name(name: str) -> str:
    """Replace invalid chars with underscores, cap at 64 chars."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


TOOL_NAME_MAP: dict[str, str] = {}


def _build_tool_definitions(tools: list[AITool]) -> list[dict[str, Any]]:
    """Convert AITool records to OpenAI-compatible tool definitions.
    Normalizes names to comply with the API's allowed character set.
    """
    global TOOL_NAME_MAP
    definitions = []
    for tool in tools:
        schema = tool.input_schema
        if not schema:
            continue
        normalized = _normalize_tool_name(tool.name)
        TOOL_NAME_MAP[normalized] = tool.name
        if normalized != tool.name:
            logger.info("Normalized tool name '%s' to '%s'", tool.name, normalized)
        definitions.append({
            "type": "function",
            "function": {
                "name": normalized,
                "description": tool.description or "",
                "parameters": schema,
            },
        })
    return definitions


async def _resolve_odoo_credentials_for_tool(db: AsyncSession, user_id: UUID) -> dict[str, str]:
    """Resolve Odoo credentials for a given user (internal tool-execution path)."""
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            AIConnectedAccount.provider == "odoo",
            or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise RuntimeError("No Odoo connected account found for tool execution")

    api_key = ""
    if account.secret_reference and os.environ.get("KEY_VAULT_URI"):
        try:
            credential = DefaultAzureCredential()
            kv_client = SecretClient(vault_url=os.environ["KEY_VAULT_URI"], credential=credential)
            secret = kv_client.get_secret(account.secret_reference)
            api_key = secret.value or ""
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve Odoo credentials from Key Vault: {e}")

    if not api_key:
        raise RuntimeError("Odoo connected account has no valid credentials")

    # Use the saved Odoo URL/DB from the connected account record.
    # Fall back to company facts or env vars for backwards compatibility.
    odoo_url = account.odoo_url or ""
    odoo_db = account.odoo_db or ""
    if not odoo_url or not odoo_db:
        url_result = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_url"))
        db_result = await db.execute(select(AICompanyFact).where(AICompanyFact.key == "odoo_primary_db"))
        url_fact = url_result.scalar_one_or_none()
        db_fact = db_result.scalar_one_or_none()
        odoo_url = url_fact.value if url_fact else os.environ.get("ODOO_URL", "")
        odoo_db = db_fact.value if db_fact else os.environ.get("ODOO_DB", "")

    if not odoo_url or not odoo_db:
        raise RuntimeError("Odoo URL or database not configured")

    logger.info("Resolved Odoo credentials for tool execution: user=%s host=%s db=%s",
                account.provider_username, odoo_url, odoo_db)

    return {
        "url": odoo_url,
        "db": odoo_db,
        "username": account.provider_username or "",
        "api_key": api_key,
        "transport": "auto",
    }


async def _execute_tool_call(
    db: AsyncSession,
    user_id: UUID,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a tool call by routing to the appropriate connector."""
    if tool_name.startswith("odoo_"):
        _log_deprecated_tool(tool_name)
        credentials = await _resolve_odoo_credentials_for_tool(db, user_id)
        path = _map_odoo_tool_to_path(tool_name)
        if not path:
            return {"error_type": "unknown_tool", "tool_name": tool_name}
        payload = {
            "credentials": credentials,
            "identity_mode": "user-delegated",
            **arguments,
        }
        url = f"{ODOO_CONNECTOR_URL.rstrip('/')}{path}" if ODOO_CONNECTOR_URL else ""
        if not url:
            return {"error": "Odoo connector URL not configured"}
        headers = {"X-Internal-API-Key": ODOO_CONNECTOR_KEY, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = {"error_type": "connector_http_error", "message": response.text}
            return {
                "error": True,
                "status_code": response.status_code,
                "connector_error": detail,
                "error_type": detail.get("error_type") or detail.get("error") or "connector_error",
                "message": detail.get("message") or detail.get("detail") or str(detail),
            }
        return response.json()

    if tool_name in ("azure_cli", "github_cli"):
        path = _map_odoo_tool_to_path(tool_name)
        if not path:
            return {"error_type": "unknown_tool", "tool_name": tool_name}
        from app.core.config import get_settings
        settings = get_settings()
        base_url = f"http://localhost:{os.environ.get('PORT', '8000')}"
        url = f"{base_url}{path}"
        payload = {**arguments}
        headers = {"X-API-Key": settings.api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = {"error_type": "connector_http_error", "message": response.text}
            return {
                "error": True,
                "status_code": response.status_code,
                "connector_error": detail,
                "error_type": "cli_error",
                "message": detail.get("message") or detail.get("detail") or str(detail),
            }
        return response.json()

    return {"error": f"Unknown tool: {tool_name}"}


REPORT_ALIASES: dict[str, str] = {
    "p&l": "Profit and Loss",
    "pnl": "Profit and Loss",
    "profit and loss": "Profit and Loss",
    "profit & loss": "Profit and Loss",
    "profit_and_loss": "Profit and Loss",
    "balance sheet": "Balance Sheet",
    "balancesheet": "Balance Sheet",
    "bs": "Balance Sheet",
    "trial balance": "Trial Balance",
    "trialbalance": "Trial Balance",
    "tb": "Trial Balance",
    "general ledger": "General Ledger",
    "generalledger": "General Ledger",
    "gl": "General Ledger",
    "partner ledger": "Partner Ledger",
    "partnerledger": "Partner Ledger",
    "aged receivables": "Aged Receivables",
    "aged_receivables": "Aged Receivables",
    "receivables aged": "Aged Receivables",
    "aged payables": "Aged Payables",
    "aged_payables": "Aged Payables",
    "payables aged": "Aged Payables",
    "tax report": "Tax Report",
    "tax_report": "Tax Report",
}

REPORT_LINE_KEYWORDS: dict[str, list[str]] = {
    "revenue": ["Revenue", "Income", "Operating Income", "Sales", "Turnover"],
    "income": ["Revenue", "Income", "Operating Income", "Sales", "Turnover"],
    "sales": ["Revenue", "Income", "Operating Income", "Sales", "Turnover"],
    "expenses": ["Expenses", "Operating Expenses", "Cost of Goods Sold", "COGS"],
    "expense": ["Expenses", "Operating Expenses", "Cost of Goods Sold", "COGS"],
    "cost": ["Cost of Goods Sold", "COGS", "Operating Expenses"],
    "cogs": ["Cost of Goods Sold"],
    "net profit": ["Net Profit", "Net Income", "Profit/Loss"],
    "net income": ["Net Profit", "Net Income", "Profit/Loss"],
    "gross profit": ["Gross Profit", "Gross Margin"],
    "gross margin": ["Gross Profit", "Gross Margin"],
    "assets": ["Assets", "Total Assets", "Current Assets", "Non-Current Assets"],
    "liabilities": ["Liabilities", "Total Liabilities", "Current Liabilities"],
    "equity": ["Equity", "Total Equity", "Owner's Equity"],
    "receivable": ["Receivables", "Accounts Receivable", "Trade Receivables"],
    "payable": ["Payables", "Accounts Payable", "Trade Payables"],
    "balance": ["Total Assets", "Total Liabilities", "Total Equity"],
}


def _detect_date_range(query: str) -> tuple[Optional[str], Optional[str]]:
    """Parse date period from a user query. Returns (date_from, date_to) or (None, None)."""
    now = datetime.utcnow()
    q = query.lower()

    if "this month" in q or "current month" in q:
        return now.replace(day=1).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")

    if "last month" in q or "previous month" in q:
        first_of_this = now.replace(day=1)
        end_of_last = first_of_this - timedelta(days=1)
        start_of_last = end_of_last.replace(day=1)
        return start_of_last.strftime("%Y-%m-%d"), end_of_last.strftime("%Y-%m-%d")

    if "this year" in q or "current year" in q or "ytd" in q or "year to date" in q:
        return now.replace(month=1, day=1).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")

    if "last year" in q or "previous year" in q:
        return f"{now.year - 1}-01-01", f"{now.year - 1}-12-31"

    if "this quarter" in q or "current quarter" in q or "q1" in q or "q2" in q or "q3" in q or "q4" in q:
        q_num = 1
        for i, label in enumerate(["q1", "q2", "q3", "q4"], 1):
            if label in q:
                q_num = i
                break
        quarter_start = {1: 1, 2: 4, 3: 7, 4: 10}[q_num]
        quarter_end = {1: 3, 2: 6, 3: 9, 4: 12}[q_num]
        year = now.year
        end_day = monthrange(year, quarter_end)[1]
        return (
            datetime(year, quarter_start, 1).strftime("%Y-%m-%d"),
            datetime(year, quarter_end, end_day).strftime("%Y-%m-%d"),
        )

    return None, None


def _detect_line_names(query: str) -> Optional[list[str]]:
    """Parse requested line names from a user query."""
    q = query.lower()
    matched = set()
    for keyword, candidates in REPORT_LINE_KEYWORDS.items():
        if keyword in q:
            matched.update(candidates)
    return list(matched) if matched else None


ODOO_LOOKUP_PATTERNS: list[tuple[re.Pattern, str, dict[str, Any]]] = [
    # "latest posted bills" / "posted bills" / "vendor bills"
    (re.compile(r'(latest\s+)?(posted\s+)?(vendor\s+)?(bill|bills|invoice|invoices)\s*', re.IGNORECASE), "bills", {
        "model": "account.move",
        "domain": [["move_type", "in", ["in_invoice", "in_receipt"]], ["state", "=", "posted"]],
        "order": "invoice_date desc",
        "limit": 10,
    }),
    # "credit note" / "credit notes" / "refund" for a partner
    (re.compile(r'(credit\s+note|credit\s+notes|refund)\s*(for|from|of)?\s*(.+?)(\?|$)', re.IGNORECASE), "credit_note", {
        "model": "account.move",
        "domain": [["move_type", "in", ["out_refund", "in_refund"]]],
        "order": "invoice_date desc",
        "limit": 10,
    }),
    # generic "find X" / "search for X" / "do you see X"
    (re.compile(r'(find|search|see|locate|look\s+(for|up))\s+(.+?)(\?|$)', re.IGNORECASE), "generic_search", {}),
    # attachment pattern: "attachments" / "PDF" / "files" on a record
    (re.compile(r'(attachment|pdf|file|document)s?\s*(on|for|attached|of)?', re.IGNORECASE), "attachment_check", {}),
]


def detect_odoo_lookup_intent(query: str) -> Optional[dict[str, Any]]:
    """Detect common Odoo lookup patterns and return deterministic actions.
    
    Returns a dict with the tool calls to execute, or None if query is not
    a detectable Odoo lookup pattern.
    """
    if not query:
        return None
    q = query.strip()

    # Check for credit-note + partner pattern
    cn_match = re.search(r'(credit\s+note|credit\s+notes|refund)\s*(?:for|from|of)?\s*(.+?)(?:\?|$|\s+with|\s+that|\s+attached)', q, re.IGNORECASE)
    if cn_match:
        partner_hint = cn_match.group(2).strip().rstrip("?.!")
        if partner_hint and not partner_hint.lower().startswith(("the ", "a ", "an ", "this ", "that ")):
            return {
                "reason": f"Found explicit credit-note + partner query for '{partner_hint}'",
                "actions": [
                    {
                        "tool": "odoo_query",
                        "input": {
                            "mode": "records",
                            "model": "account.move",
                            "domain": [
                                ["move_type", "in", ["out_refund", "in_refund"]],
                                "|",
                                ["partner_id.name", "ilike", partner_hint],
                                ["partner_id.display_name", "ilike", partner_hint],
                            ],
                            "fields": ["id", "name", "partner_id", "invoice_date", "amount_total", "state", "move_type", "ref"],
                            "order": "invoice_date desc",
                            "limit": 10,
                        },
                    },
                ],
            }
        # Credit note without explicit partner
        return {
            "reason": "Found credit-note query (no explicit partner)",
            "actions": [
                {
                    "tool": "odoo_query",
                    "input": {
                        "mode": "records",
                        "model": "account.move",
                        "domain": [["move_type", "in", ["out_refund", "in_refund"]]],
                        "fields": ["id", "name", "partner_id", "invoice_date", "amount_total", "state", "move_type"],
                        "order": "invoice_date desc",
                        "limit": 10,
                    },
                },
            ],
        }

    # Check for bill/invoice search
    bill_match = re.search(r'(?:latest\s+|posted\s+|vendor\s+)?(?:bill|bills|invoice|invoices)(?:\s|$|for|from)', q, re.IGNORECASE)
    if bill_match:
        partner_hint2 = None
        for prefix in ["for ", "from ", "by "]:
            idx = q.lower().find(prefix)
            if idx >= 0:
                partner_hint2 = q[idx + len(prefix):].strip().rstrip("?.!")
                break
        domain = [["move_type", "in", ["in_invoice", "in_receipt"]], ["state", "=", "posted"]]
        if partner_hint2 and len(partner_hint2) > 2:
            domain = [
                ["move_type", "in", ["in_invoice", "in_receipt"]],
                ["state", "=", "posted"],
                "|",
                ["partner_id.name", "ilike", partner_hint2],
                ["partner_id.display_name", "ilike", partner_hint2],
            ]
        return {
            "reason": f"Found bill/invoice query{' for ' + partner_hint2 if partner_hint2 else ''}",
            "actions": [
                {
                    "tool": "odoo_query",
                    "input": {
                        "mode": "records",
                        "model": "account.move",
                        "domain": domain,
                        "fields": ["id", "name", "partner_id", "invoice_date", "amount_total", "state", "move_type"],
                        "order": "invoice_date desc",
                        "limit": 10,
                    },
                },
            ],
        }

    # Check for attachment pattern on a known record
    attach_match = re.search(r'(?:attachment|pdf|file|document)s?\s*(?:on|for|attached|of)?\s*(.+?)(?:\?|$)', q, re.IGNORECASE)
    if attach_match and "attachment" in q.lower() or "pdf" in q.lower() or "attached" in q.lower():
        # If we're asking about attachments, we need to first find the record
        # This is handled by the model, not deterministically
        pass

    return None


def detect_odoo_report_intent(query: str) -> Optional[dict[str, Any]]:
    """Detect a generic Odoo report intent from a user query.
    
    Returns tool arguments dict for odoo_execute_report if a report is detected,
    or None if the query does not appear to be about Odoo reports.
    """
    if not query:
        return None
    q = query.lower().strip()

    report_name = None
    for alias, canonical in REPORT_ALIASES.items():
        if alias in q:
            report_name = canonical
            break
    if not report_name:
        return None

    date_from, date_to = _detect_date_range(q)
    line_names = _detect_line_names(q)

    args: dict[str, Any] = {"report_name": report_name}
    if date_from and date_to:
        args["date_from"] = date_from
        args["date_to"] = date_to
    if line_names:
        args["line_names"] = line_names

    logger.info(
        "Detected Odoo report intent | query=%s report_name=%s date_from=%s date_to=%s line_names=%s",
        query[:80], report_name, date_from, date_to, line_names,
    )
    return {"tool": "odoo_ops_runner", "input": {"mode": "report", **args}}


def _build_report_fallback_answer(tool_results: list[dict]) -> str | None:
    """Build a fallback user-facing answer from odoo_execute_report tool results.
    Used when the model produces blank content after a report tool call.
    All user-facing strings are clean — no raw dicts or Python repr."""
    for tr in tool_results:
        if tr.get("tool_name") not in ("odoo_execute_report", "odoo_list_reports"):
            continue
        result = tr.get("result", {})
        if isinstance(result, dict) and result.get("error"):
            connector_err = result.get("connector_error") or result
            detail = connector_err.get("detail", connector_err) if isinstance(connector_err, dict) else {}
            raw_message = detail.get("message") or detail.get("detail") or connector_err.get("message") or str(connector_err)
            err_type = (
                detail.get("error_type")
                or (detail.get("error") if isinstance(detail.get("error"), str) else None)
                or connector_err.get("error_type")
                or "report_error"
            )

            if err_type == "report_not_found":
                report_name = tr.get("arguments", {}).get("report_name", "unknown")
                return (
                    f"I could not find a report named \"{report_name}\" in Odoo. "
                    f"This may be because the report module is not installed or the name is different. "
                    f"Try using the report discovery tool to list available reports."
                )
            if "Technical error" in raw_message:
                return (
                    f"I reached Odoo, but could not execute the report. "
                    f"The report engine encountered an internal issue: {raw_message}. "
                    f"This usually means the report could not be resolved or executed "
                    f"with the current Odoo account. Please check Accounting report access, "
                    f"Odoo edition/version, or use the report discovery diagnostic "
                    f"to confirm the available report names."
                )
            return (
                f"I reached Odoo, but could not execute the report. "
                f"Reason: {raw_message}. "
                f"This may be due to report permissions, Odoo edition/version differences, "
                f"or unsupported report options."
            )
        if isinstance(result, dict) and not result.get("error"):
            lines = result.get("lines") or []
            report_name = result.get("report_name") or "report"
            currency_code = result.get("currency_code") or ""
            currency_symbol = result.get("currency_symbol") or ""
            date_from = result.get("date_from") or ""
            date_to = result.get("date_to") or ""
            available = result.get("available_line_names") or []
            missing = result.get("missing_line_names") or []
            if lines:
                parts = [f"From the Odoo {report_name}"]
                if date_from and date_to:
                    parts.append(f"for {date_from} to {date_to}")
                parts.append(":")
                line_items = []
                for ln in lines[:10]:
                    name = ln.get("name", "")
                    val = ln.get("formatted_value") or ""
                    if name and val:
                        sym = currency_symbol or ""
                        line_items.append(f"{name}: {sym}{val}")
                    elif name:
                        line_items.append(f"{name}")
                if line_items:
                    parts.append("")
                    parts.extend(f"  - {li}" for li in line_items)
                if len(lines) > 10:
                    parts.append(f"  ... and {len(lines) - 10} more lines")
                if missing:
                    parts.append(f"Note: requested lines not found in report: {', '.join(missing[:5])}")
                return "\n".join(parts)
            if available:
                return (
                    f"I opened the {report_name} report"
                    f"{' for ' + date_from + ' to ' + date_to if date_from and date_to else ''}, "
                    f"but could not find matching lines. "
                    f"Available top-level lines include: {', '.join(available[:10])}."
                )
    return None


DEPRECATED_TOOL_ALIASES: dict[str, str] = {
    "odoo_search_read": "odoo_query",
    "odoo_execute_report": "odoo_analyze",
    "odoo_attachments_list": "odoo_query",
    "odoo_attachments_get": "odoo_attachment",
    "odoo_messages_list": "odoo_content",
    "odoo_messages_create": "odoo_message",
}


def _log_deprecated_tool(tool_name: str):
    replacement = DEPRECATED_TOOL_ALIASES.get(tool_name)
    if replacement:
        logger.warning("Deprecated tool '%s' called — delegate to '%s' instead", tool_name, replacement)


def _get_connector_url_for_tool(tool_name: str) -> str:
    """Return the Odoo Connector URL for Odoo tools, or empty for native tools."""
    if tool_name.startswith("odoo_"):
        return ODOO_CONNECTOR_URL
    return ""


def _map_odoo_tool_to_path(tool_name: str) -> str:
    mapping = {
        # Primary consolidated tools
        "odoo_ops_runner": "/odoo/ops/run",
        # Legacy individual Odoo tools (still work as compatibility aliases)
        "odoo_health": "/odoo/ops/run",
        "odoo_schema": "/odoo/ops/run",
        "odoo_query": "/odoo/ops/run",
        "odoo_analyze": "/odoo/ops/run",
        "odoo_content": "/odoo/ops/run",
        "odoo_attachment": "/odoo/ops/run",
        "odoo_mutation": "/odoo/ops/run",
        "odoo_message": "/odoo/ops/run",
        "odoo_execute_report": "/reports/execute",
        "odoo_list_reports": "/reports/list",
        "odoo_search_read": "/records/search-read",
        "odoo_execute_kw": "/execute-kw/",
        "odoo_attachments_list": "/attachments/list",
        "odoo_attachments_get": "/attachments/get",
        "odoo_messages_list": "/messages/list",
        "odoo_messages_create": "/messages/create",
        # Native connectors (routed differently)
        "azure_cli": "/connector/azure/cli",
        "github_cli": "/connector/github/cli",
    }
    return mapping.get(tool_name, "")


async def _get_connector_context(db: AsyncSession, user_id: Optional[UUID]) -> str:
    """Build a connector-availability context block for the current user.

    Queries AIConnectedAccount for the user and returns a human-readable
    block that the model can use to know which systems are actually available.
    """
    lines: list[str] = ["Connected Account Status:"]
    if not user_id:
        lines.append("  (no authenticated user context)")
        return "\n".join(lines)

    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
        )
    )
    accounts = result.scalars().all()

    conn_map: dict[str, str] = {}
    for acct in accounts:
        status = acct.status
        if status == "connected":
            conn_map[acct.provider] = "connected"
        elif status == "active":
            conn_map[acct.provider] = "connected"
        else:
            conn_map[acct.provider] = status

    for conn_type in KNOWN_CONNECTOR_TYPES:
        display_name = CONNECTOR_DISPLAY_NAMES.get(conn_type, conn_type.replace("_", " ").title())
        if conn_type in conn_map:
            status = conn_map[conn_type]
            icon = "✓" if status == "connected" else "✗"
            lines.append(f"  {icon} {display_name}: {status}")
        else:
            lines.append(f"  - {display_name}: not connected")

    return "\n".join(lines)


async def execute_chat(
    db: AsyncSession,
    messages: list,
    task_type: str = "general_chat",
    chat_session_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
) -> dict:
    # Use ModelRoutingPolicyService to determine primary route and fallback properties
    from app.services.model_routing_policy import ModelRoutingPolicyService
    from app.core.config import get_settings
    routing_svc = ModelRoutingPolicyService(db)

    # Analyze risk level based on task_type and text content
    # Simple Odoo field lookups mentioning "amount" or "total" as field references
    # should not be classified as high-risk finance.
    user_msg_text = messages[-1]["content"] if messages else ""
    is_finance_topic = any(kw in user_msg_text.lower() for kw in [
        "revenue", "income", "expense", "profit", "loss", "balance", "invoice",
        "bill", "payment", "cost", "price", "tax", "vat", "accounting",
    ])

    # If finance keywords match but the query is an Odoo/ERP lookup asking for
    # field values (amount, total), don't escalate — treat as data retrieval.
    is_odoo_lookup = any(phrase in user_msg_text.lower() for phrase in [
        "check odoo", "odoo", "account.move", "ir.attachment", "credit note",
        "find", "search", "look up",
    ])
    if is_odoo_lookup and user_msg_text.lower().count("amount") <= 2:
        contains_aggregate_intent = any(kw in user_msg_text.lower() for kw in [
            "compare", "reconcile", "audit", "forecast", "budget", "analyze",
            "trend", "variance", "total revenue", "total income", "net profit",
        ])
        if not contains_aggregate_intent:
            is_finance_topic = False

    risk_level = "high" if is_finance_topic else "low"

    policy = await routing_svc.select_route(
        task_type=task_type,
        risk_level=risk_level,
        requires_tools=True if task_type == "general_chat" else False
    )

    route_id_str = policy.get("selected_route_id")
    model_id_str = policy.get("selected_model_id")

    if route_id_str and model_id_str:
        # Load selected route
        route_res = await db.execute(select(AIRoute).where(AIRoute.id == UUID(route_id_str)))
        route = route_res.scalar_one_or_none()

        # Load selected model
        model_res = await db.execute(select(AIModel).where(AIModel.id == UUID(model_id_str)))
        model_obj = model_res.scalar_one_or_none()

        # Load provider
        prov_res = await db.execute(select(AIProvider).where(AIProvider.id == model_obj.provider_id))
        provider = prov_res.scalar_one_or_none()
    else:
        # Fallback to legacy get_enabled_route if none found in DB
        route, model_obj, provider = await get_enabled_route(db, task_type)

    # Build system prompt with dynamic connector context
    system_prompt = route.system_prompt or ""
    connector_context = await _get_connector_context(db, user_id)
    if connector_context:
        system_prompt = system_prompt.rstrip() + "\n\n" + connector_context

    # Fetch available tools for connected systems using generic ToolSelectionService
    tools: list[AITool] = []
    tool_definitions: list[dict[str, Any]] = []
    tool_selection_result = None
    supports_tools = model_obj.supports_tools == "true"
    if supports_tools:
        from app.services.tool_selection import get_tool_selection
        tool_selection_result = await get_tool_selection(
            db, user_id, user_msg_text, task_type, risk_level,
        )
        tools = tool_selection_result.selected
        tool_definitions = _build_tool_definitions(tools)
        if tool_selection_result.selected:
            logger.info(
                "Tool selection | intent=%s selected=%d excluded=%d schema_before=%d schema_after=%d reason=%s",
                tool_selection_result.intent,
                len(tool_selection_result.selected),
                len(tool_selection_result.excluded),
                tool_selection_result.schema_size_before,
                tool_selection_result.schema_size_after,
                tool_selection_result.selection_reason,
            )
        if tool_definitions:
            avail_names = [t.name for t in tools]
            system_prompt += (
                "\n\nYou have access to the following tools. "
                "When the user asks about data from a connected system, call the appropriate tool "
                "rather than saying you cannot access it. "
                "Use tools proactively when relevant."
            )
            odoo_avail = [n for n in avail_names if n.startswith("odoo_")]
            if odoo_avail:
                guidance_parts = ["\n\n### Odoo Tool Guidance\nUse these mode-based Odoo tools. Do not create one-off tools.\n"]
                tool_descriptions = {
                    "odoo_query": "Records/count/summary. Default to records with domain.",
                    "odoo_analyze": "Aggregates and account reports. P&L → Profit and Loss.",
                    "odoo_content": "Chatter/notes/long text. metadata first, then content with IDs.",
                    "odoo_attachment": "Attachment metadata and text. Discovery via odoo_query on ir.attachment.",
                    "odoo_mutation": "Create/write/delete/workflow. dry_run for delete/workflow.",
                    "odoo_message": "Post/update chatter/Discuss messages.",
                    "odoo_schema": "Model/field discovery when unsure.",
                    "odoo_health": "Connection/runtime check.",
                }
                for name in odoo_avail:
                    desc = tool_descriptions.get(name, "")
                    if desc:
                        guidance_parts.append(f"  - **{name}**: {desc}")
                if "odoo_analyze" in odoo_avail:
                    guidance_parts.append(
                        "Report aliases: P&L/PNL→Profit and Loss, BS/Balance Sheet, TB/Trial Balance, GL/General Ledger.\n"
                        "Dates: this month→first day to today; this year→Jan 1 to today; last month→previous month.\n"
                        "Line names: revenue→[Revenue, Income, Sales]; expenses→[Expenses, COGS]; net income→[Net Profit, Net Income]."
                    )
                guidance_parts.append("Do not use odoo_execute_kw (disabled). Do not create one-off tools.")
                system_prompt += "\n".join(guidance_parts)

    # Inject business rules and company facts into the system prompt
    injected_rules: list[Any] = []
    injected_facts: list[Any] = []
    try:
        connected_systems: set[str] = set()
        if user_id:
            acct_result = await db.execute(
                select(AIConnectedAccount).where(
                    AIConnectedAccount.user_id == user_id,
                    or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
                )
            )
            connected_systems = {a.provider for a in acct_result.scalars().all()}
        context_svc = ContextService(db)
        context = await context_svc.get_context(
            ContextRequest(
                task="general_chat",
                systems=list(connected_systems) if connected_systems else None,
                limit=50,
            ),
            user_id=user_id,
        )
        injected_rules = context.get("rules", [])
        injected_facts = context.get("facts", [])
        if injected_rules:
            rules_text = "\n".join(
                f"- [Priority {r.priority}] {r.body}" for r in injected_rules
            )
            system_prompt += f"\n\n## Active Business Rules\n{rules_text}"
        if injected_facts:
            facts_text = "\n".join(
                f"- {f.key}: {f.value}" for f in injected_facts
            )
            system_prompt += f"\n\n## Company Facts\n{facts_text}"
        # Inject Odoo company currency if available
        odoo_currency_str = None
        currency_source = "none"
        if user_id:
            acct_result2 = await db.execute(
                select(AIConnectedAccount).where(
                    AIConnectedAccount.user_id == user_id,
                    AIConnectedAccount.provider == "odoo",
                    or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
                )
            )
            odoo_account = acct_result2.scalars().first()
            if odoo_account and odoo_account.odoo_currency_code:
                code = odoo_account.odoo_currency_code
                symbol = odoo_account.odoo_currency_symbol or code
                company = odoo_account.odoo_company_name or "your company"
                odoo_currency_str = f"{company} uses {code} ({symbol})"
                system_prompt += f"\n\n## Connected Odoo Currency\n{odoo_currency_str}"
                currency_source = "odoo_connected_account"

        # Inject active memories (learned preferences, patterns, resolved cases)
        injected_memories: list[Any] = []
        try:
            if user_id:
                mem_result = await db.execute(
                    select(AIMemory).where(
                        AIMemory.created_by_user_id == user_id,
                        AIMemory.status == "active",
                    )
                    .order_by(AIMemory.priority.asc(), AIMemory.last_used_at.desc().nullslast())
                    .limit(30)
                )
                injected_memories = mem_result.scalars().all()
                if injected_memories:
                    mem_blocks: list[str] = []
                    for m in injected_memories:
                        formatted = f"- [{m.type}] {m.title}"
                        if m.summary:
                            formatted += f": {m.summary}"
                        if m.body:
                            formatted += f"\n  Detail: {m.body[:300]}"
                        mem_blocks.append(formatted)
                    system_prompt += "\n\n## Learned from Past Interactions\n" + "\n".join(mem_blocks)
        except Exception as mem_exc:
            logger.warning("Failed to inject memories: %s", mem_exc)

        # Search Azure AI Search for relevant SOPs, procedures, and long documents
        chunks_to_inject: list[dict[str, Any]] = []
        try:
            from app.services.search_service import SearchService
            search_svc = SearchService()
            if search_svc.enabled and messages:
                user_query = messages[-1]["content"]
                # Query Azure AI Search with filters
                injected_search_results = await search_svc.search_memories(
                    query=user_query,
                    user_id=user_id,
                    status="active"
                )

                # Check feature flag and limit max injected chunks
                from app.core.config import get_settings
                max_chunks = get_settings().azure_search_max_injected_chunks
                chunks_to_inject = injected_search_results[:max_chunks]

                if chunks_to_inject:
                    search_blocks = []
                    for hit in chunks_to_inject:
                        source_type = hit.get("type") or hit.get("source_type") or "reference"
                        title = hit.get("title") or "Untitled Document"
                        chunk_text = hit.get("chunk_text") or hit.get("summary") or ""
                        block = f"- [{source_type}] {title}"
                        if chunk_text:
                            block += f"\n  Details: {chunk_text[:350]}"
                        search_blocks.append(block)

                    system_prompt += "\n\n## Relevant Reference Materials\n" + "\n".join(search_blocks)
        except Exception as search_exc:
            logger.warning("Failed to retrieve or inject search results: %s", search_exc)

        # Check if the user query is complex and requires Orchestrator task-graph node execution
        subtasks_data: list[dict[str, Any]] = []
        if messages:
            user_query = messages[-1]["content"]
            is_reconciliation = any(kw in user_query.lower() for kw in ["compare", "reconcile", "reconciliation", "credit note", "pdf"])
            if is_reconciliation:
                try:
                    from app.services.task_graph import TaskGraphExecutor
                    executor = TaskGraphExecutor()
                    subtasks_data = await executor.execute_all(user_query, db=db)

                    # Inject subtask results into system prompt
                    subtask_summary = []
                    for t in subtasks_data:
                        subtask_summary.append(f"- Subtask '{t['name']}' ({t['status']}): Result={t['result']}")

                    system_prompt += "\n\n## Ephemeral Sub-Agent / Task Worker Results\n" + "\n".join(subtask_summary)
                except Exception as t_exc:
                    logger.warning("Failed to execute Task Graph nodes: %s", t_exc)

        logger.info(
            "Context injected | rules=%d facts=%d memories=%d search_results=%d subtasks=%d user_id=%s currency=%s",
            len(injected_rules), len(injected_facts), len(injected_memories), len(chunks_to_inject), len(subtasks_data), user_id, odoo_currency_str or "none",
        )
    except Exception as exc:
        logger.warning("Failed to inject context: %s", exc)

    if system_prompt:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
    else:
        full_messages = messages

    temperature = float(route.temperature) if route.temperature is not None else 0.3
    max_tokens = route.max_tokens or 2000

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_latency_ms = 0
    total_tool_calls = 0
    tool_results: list[dict[str, Any]] = []

    async def _try_model(model, prov, msgs):
        cl = await build_foundry_client(prov, model)
        res = await cl.chat_completion(
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tool_definitions if tool_definitions else None,
        )
        return res, model, prov, cl

    result, used_model, used_provider, client = await _try_model(model_obj, provider, full_messages)
    total_prompt_tokens += result.get("prompt_tokens", 0)
    total_completion_tokens += result.get("completion_tokens", 0)
    total_latency_ms += result.get("latency_ms", 0)

    # Fallback on quota / rate-limit errors — try ALL available tool-supporting models
    fallback_used = False
    primary_model_display = model_obj.display_name
    fallback_model_display = "none"
    fallback_reason = "noneeded"

    if result.get("error") and result.get("error_type") in ("rate_limit_exceeded", "quota_exceeded"):
        fallback_reason = "primary_quota_exceeded"
        # Collect all possible fallback models: configured fallback first, then any other enabled model
        fallback_candidates: list[tuple[Any, Any]] = []
        
        # 1. Try configured fallback model first
        if route.fallback_model_id:
            fb_model_res = await db.execute(
                select(AIModel).where(AIModel.id == route.fallback_model_id, AIModel.enabled == "true")
            )
            fb_model = fb_model_res.scalar_one_or_none()
            if fb_model:
                fb_prov_res = await db.execute(
                    select(AIProvider).where(AIProvider.id == fb_model.provider_id, AIProvider.enabled == "true")
                )
                fb_prov = fb_prov_res.scalar_one_or_none()
                if fb_prov:
                    fallback_candidates.append((fb_model, fb_prov))

        # 2. If configured fallback doesn't support tools, try all other enabled models with tools
        needs_tools = bool(tool_definitions)
        if needs_tools and (not fallback_candidates or fallback_candidates[0][0].supports_tools != "true"):
            all_models_res = await db.execute(
                select(AIModel).where(
                    AIModel.enabled == "true",
                    AIModel.id != model_obj.id,
                    AIModel.id != (route.fallback_model_id or UUID(int=0)),
                ).limit(10)
            )
            for alt_model in all_models_res.scalars().all():
                if alt_model.supports_tools == "true" or (alt_model.config_json or {}).get("supports_tools") is True:
                    alt_prov_res = await db.execute(
                        select(AIProvider).where(AIProvider.id == alt_model.provider_id, AIProvider.enabled == "true")
                    )
                    alt_prov = alt_prov_res.scalar_one_or_none()
                    if alt_prov:
                        fallback_candidates.append((alt_model, alt_prov))

        for fb_model, fb_prov in fallback_candidates:
            fb_supports_tools = fb_model.supports_tools == "true" or (fb_model.config_json or {}).get("supports_tools") is True
            if needs_tools and not fb_supports_tools:
                logger.warning("Fallback candidate %s does not support required tools. Skipping.", fb_model.display_name)
                continue

            fallback_model_display = fb_model.display_name
            logger.warning(
                "Primary model quota exceeded, trying fallback | primary=%s fallback=%s",
                model_obj.display_name, fb_model.display_name,
            )
            fb_result, used_model, used_provider, client = await _try_model(fb_model, fb_prov, full_messages)
            total_prompt_tokens += fb_result.get("prompt_tokens", 0)
            total_completion_tokens += fb_result.get("completion_tokens", 0)
            total_latency_ms += fb_result.get("latency_ms", 0)
            result = fb_result
            fallback_used = True
            if not result.get("error"):
                break  # Success — stop trying more fallbacks
            fallback_reason = f"tried_fallback_{fb_model.display_name}_also_failed"

    # If primary model failed with quota and no tool-capable fallback was found,
    # try deterministic Odoo lookup for Odoo-related queries.
    if result.get("error") and result.get("error_type") in ("rate_limit_exceeded", "quota_exceeded") and not fallback_used:
        user_query = messages[-1].get("content", "") if messages else ""
        lookup_intent = detect_odoo_lookup_intent(user_query) if user_query else None
        if lookup_intent:
            fallback_reason = f"deterministic_odoo_lookup_fallback_from_{result.get('error_type')}"
            logger.info(
                "Using deterministic Odoo lookup fallback | user_id=%s reason=%s",
                user_id, fallback_reason,
            )
            # Execute the deterministic actions
            dr_tool_results = []
            all_ok = True
            for action in lookup_intent.get("actions", []):
                try:
                    tc_result = await _execute_tool_call(db, user_id, action["tool"], action["input"])
                    dr_tool_results.append({
                        "tool_call_id": f"deterministic_{action['tool']}",
                        "tool_name": action["tool"],
                        "arguments": action["input"],
                        "result": tc_result,
                    })
                    if isinstance(tc_result, dict) and tc_result.get("error"):
                        all_ok = False
                        break
                except Exception as act_exc:
                    logger.error("Deterministic Odoo action failed: %s", act_exc)
                    all_ok = False
                    break
            if dr_tool_results:
                tool_results = dr_tool_results
                fallback_used = True
                # Build answer from tool results
                dr_fallback = _build_report_fallback_answer(dr_tool_results)
                if dr_fallback:
                    result = {"content": dr_fallback, "finish_reason": "stop", "error": False}
                else:
                    # Build a simple summary from results
                    result_parts = []
                    for tr in dr_tool_results:
                        r = tr.get("result", {})
                        if isinstance(r, dict) and not r.get("error"):
                            records = r.get("records", r.get("lines", r.get("results", [])))
                            if records:
                                result_parts.append(f"{tr['tool_name']}: found {len(records)} records")
                            else:
                                result_parts.append(f"{tr['tool_name']}: no results")
                        elif isinstance(r, dict) and r.get("error"):
                            result_parts.append(f"{tr['tool_name']}: error - {r.get('message', 'unknown')}")
                    result = {"content": "; ".join(result_parts) if result_parts else "Odoo lookup completed but no results found.", "finish_reason": "stop", "error": False}

    # Tool-calling loop (up to 10 rounds to prevent infinite loops)
    max_tool_rounds = 10
    tool_round = 0
    while tool_round < max_tool_rounds:
        if result.get("error"):
            break

        tool_calls = result.get("tool_calls")
        if not tool_calls or not tools:
            break

        tool_round += 1
        total_tool_calls += len(tool_calls)

        assistant_msg = {"role": "assistant", "content": result.get("content") or None}
        assistant_msg["tool_calls"] = [
            {"id": tc["id"], "type": tc["type"], "function": tc["function"]}
            for tc in tool_calls
        ]
        full_messages.append(assistant_msg)

        for tc in tool_calls:
            if tc.get("type") != "function":
                continue
            func = tc.get("function", {})
            name = func.get("name", "")
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}

            tc_result = await _execute_tool_call(db, user_id, name, args)
            tool_results.append({
                "tool_call_id": tc.get("id", ""),
                "tool_name": name,
                "arguments": args,
                "result": tc_result,
            })

            full_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": json.dumps(tc_result),
            })

        result = await client.chat_completion(
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tool_definitions if tool_definitions else None,
        )

        total_prompt_tokens += result.get("prompt_tokens", 0)
        total_completion_tokens += result.get("completion_tokens", 0)
        total_latency_ms += result.get("latency_ms", 0)

    log = AIUsageLog(
        provider_id=used_provider.id,
        model_id=used_model.id,
        route_id=route.id,
        task_type=task_type,
        chat_session_id=chat_session_id,
        user_id=user_id,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        total_tokens=total_prompt_tokens + total_completion_tokens,
        latency_ms=total_latency_ms,
        status="failed" if result.get("error") else "success",
        error_message=result.get("message") if result.get("error") else None,
    )
    db.add(log)
    await db.flush()

    if result.get("error"):
        error_type = result.get("error_type", "unknown")
        raw_message = result.get("message", "Provider returned an error")
        status_code = result.get("status_code", 0)

        logger.error(
            "Provider call failed | primary_model=%s primary_provider=%s "
            "used_model=%s used_provider=%s "
            "error_type=%s status_code=%s raw_message=%s "
            "fallback_used=%s fallback_model=%s "
            "user_id=%s chat_session_id=%s tools_enabled=%s",
            model_obj.display_name,
            provider.name,
            used_model.display_name,
            used_provider.name,
            error_type,
            status_code,
            raw_message,
            fallback_used,
            fallback_model_display,
            user_id,
            chat_session_id,
            bool(tool_definitions),
        )

        if error_type in ("rate_limit_exceeded", "quota_exceeded"):
            if fallback_used:
                user_facing = (
                    f"The AI service is temporarily unavailable because all models "
                    f"reached their quota or rate limit. "
                    f"Tried: {primary_model_display} (primary) and {fallback_model_display} (fallback). "
                    f"Please try again shortly, or contact support if this continues."
                )
            else:
                fb_note = f" (fallback model: {fallback_model_display})" if fallback_model_display != "none" else ""
                user_facing = (
                    f"The AI service is temporarily unavailable because the model "
                    f"quota or rate limit has been reached. "
                    f"Primary: {primary_model_display}{fb_note}. "
                    f"Please try again shortly, or contact support if this continues."
                )
        else:
            user_facing = {
                "authentication_error": (
                    "The AI service is unavailable due to an authentication issue. "
                    "Please contact support."
                ),
                "authorization_error": (
                    "The AI service is unavailable due to an authorization issue. "
                    "Please contact support."
                ),
                "model_not_found": (
                    "The configured AI model could not be found. "
                    "Please contact support."
                ),
                "server_error": (
                    "The AI service is temporarily unavailable. "
                    "Please try again shortly, or contact support if this continues."
                ),
                "bad_request": (
                    "The AI service received an invalid request. "
                    "Please try again, or contact support if this continues."
                ),
            }.get(error_type, (
                "The AI service is temporarily unavailable. "
                "Please try again shortly, or contact support if this continues."
            ))

        raise ProviderCallError(
            user_facing,
            used_provider.name,
            used_model.display_name,
        )

    context_metadata = {
        "rules_injected": [{"id": str(r.id), "title": r.title, "priority": r.priority} for r in injected_rules] if injected_rules else [],
        "facts_injected": [{"key": f.key, "value": f.value} for f in injected_facts] if injected_facts else [],
        "memories_injected": [{"id": str(m.id), "title": m.title, "type": m.type} for m in injected_memories] if injected_memories else [],
        "search_results_injected": [
            {
                "id": hit.get("id"),
                "title": hit.get("title"),
                "type": hit.get("type"),
                "score": hit.get("score")
            }
            for hit in chunks_to_inject
        ] if chunks_to_inject else [],
        "currency_source": currency_source,
        "subtasks": subtasks_data if "subtasks_data" in locals() else [],
        "model_routing": {
            "primary_model": primary_model_display,
            "fallback_model": fallback_model_display,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "routing_reason": policy.get("reason", "unknown") if "policy" in locals() else "legacy_get_enabled_route",
            "cost_tier": policy.get("cost_tier", "medium") if "policy" in locals() else "medium"
        }
    }

    response = {
        "content": result.get("content", ""),
        "finish_reason": result.get("finish_reason", ""),
        "model_provider": used_provider.name,
        "model_name": used_model.display_name,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "latency_ms": total_latency_ms,
        "tool_calls": tool_results if tool_results else None,
        "context": context_metadata,
        "tool_call_count": total_tool_calls,
    }

    # If content is blank, try deterministic fallback paths
    content = response.get("content") or ""
    if not content.strip():
        if tool_results:
            fallback = _build_report_fallback_answer(tool_results)
            if fallback:
                logger.info(
                    "Used report fallback answer (from tool results) | user_id=%s tool_calls=%d",
                    user_id, len(tool_results),
                )
                response["content"] = fallback
        elif messages:
            user_query = messages[-1].get("content", "") if isinstance(messages[-1], dict) else ""
            report_intent = detect_odoo_report_intent(user_query) if user_query else None
            if report_intent:
                logger.info(
                    "Deterministic report intent detected | user_id=%s intent=%s",
                    user_id, report_intent,
                )
                try:
                    tc_result = await _execute_tool_call(db, user_id, "odoo_analyze", report_intent["input"])
                    dr_tool_results = [{
                        "tool_call_id": "deterministic_report",
                        "tool_name": "odoo_execute_report",
                        "arguments": report_intent["input"],
                        "result": tc_result,
                    }]
                    dr_fallback = _build_report_fallback_answer(dr_tool_results)
                    if dr_fallback:
                        response["content"] = dr_fallback
                        response["tool_calls"] = dr_tool_results
                        response["deterministic_report"] = True
                        logger.info(
                            "Used deterministic report answer | user_id=%s", user_id,
                        )
                except Exception as drexc:
                    logger.error("Deterministic report execution failed: %s", drexc)

    return response
