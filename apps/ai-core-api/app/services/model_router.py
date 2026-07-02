import os
import re
import json
import logging
from dataclasses import dataclass, field
from uuid import UUID
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional, Any
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.models.models import (
    AIProvider, AIModel, AIRoute, AIUsageLog, AIConnectedAccount, AITool,
    AIMemory, AIArtifact,
)
from app.services.model_provider_client import ModelProviderClient
from app.services.key_vault import get_secret_value, key_vault_uri
from app.services.connected_account_state import effective_connected_accounts, upsert_delegated_account
from app.services.model_tool_calls import (
    _build_tool_definitions,
)
from app.services.tool_registry import (
    MICROSOFT_NATIVE_CONNECTOR_SYSTEMS,
    MICROSOFT_NATIVE_TOOL_NAMES,
)
from app.services.tool_guidance import tool_guidance_payload, tool_skill_markdown
from app.services.workspace_runtime import WORKSPACE_TOOL_NAME, WorkspaceSession, run_workspace

logger = logging.getLogger(__name__)

ROUTE_NOT_CONFIGURED_MESSAGE = "AI chat is not configured yet. Please ask an administrator to configure AI Providers."
MAX_TOOL_RESULT_STRING_CHARS = 600
MAX_TOOL_STDIO_STRING_CHARS = 20000
MAX_TOOL_RESULT_LIST_ITEMS = 5
MAX_TOOL_RESULT_RECORD_ITEMS = 120
MAX_TOOL_RESULT_DICT_KEYS = 60
MAX_TOOL_RESULT_JSON_CHARS = 350000
DOCUMENT_READER_DEFAULT_LIMIT = 500
DOCUMENT_READER_MAX_LIMIT = 2000
DOCUMENT_READER_MAX_CHARS = 100000
DOCUMENT_READER_DEFAULT_TABLE_LIMIT = 20
DOCUMENT_READER_MAX_TABLE_LIMIT = 100
DOCUMENT_READER_DEFAULT_PAGE_LIMIT = 20
DOCUMENT_READER_MAX_PAGE_LIMIT = 100
TOOL_LOOP_RESPONSE_MAX_TOKENS = int(os.environ.get("TOOL_LOOP_RESPONSE_MAX_TOKENS", "3000"))
TOOL_LOOP_LENGTH_CONTINUATION_LIMIT = 3
MAX_TOOL_LOOP_ITERATIONS = int(os.environ.get("MAX_TOOL_LOOP_ITERATIONS", "20"))
TOOL_ERROR_SUMMARY_LIMIT = 8
CONNECTOR_SKILL_MAX_CHARS = int(os.environ.get("CONNECTOR_SKILL_MAX_CHARS", "24000"))
TOOL_SKILL_MAX_CHARS = int(os.environ.get("TOOL_SKILL_MAX_CHARS", "20000"))
CONNECTOR_SKILL_TIMEOUT_SECONDS = float(os.environ.get("CONNECTOR_SKILL_TIMEOUT_SECONDS", "8"))
TOOL_LOOP_FOLLOWUP_MESSAGE = {
    "role": "system",
    "content": (
        "Use the tool results already gathered to answer the user. "
        "Call another tool only when a necessary fact is still missing. "
        "Only state connected-system facts that are present in successful tool output. "
        "If another tool is needed, call it now instead of saying you will check next."
    ),
}
TOOL_LOOP_BLANK_LENGTH_RETRY_MESSAGE = {
    "role": "system",
    "content": (
        "Your previous response used the answer budget without producing visible assistant content. "
        "Produce the final answer now using the gathered tool results. Do not call tools. "
        "Do not include reasoning or analysis. If the user asked for a full table, output the full table."
    ),
}
TOOL_LOOP_CONTINUE_LENGTH_MESSAGE = {
    "role": "system",
    "content": (
        "Continue the visible answer exactly where it stopped. Do not repeat completed rows or headings. "
        "Do not call tools. Do not include reasoning or analysis."
    ),
}
TOOL_LOOP_LIMIT_ANSWER_MESSAGE = {
    "role": "system",
    "content": (
        "The tool step limit has been reached. Do not call more tools. "
        "Answer the user from the successful tool results already present. "
        "If the gathered results are insufficient, say exactly what is missing without guessing."
    ),
}
OMITTED_TOOL_CONTENT_KEYS = {
    "datas",
    "raw",
    "raw_html",
    "html",
    "content_base64",
    "base64",
    "binary",
}
LARGE_TEXT_TOOL_KEYS = {
    "body",
    "content",
    "description",
    "index_content",
    "message_body",
    "note",
    "text",
}

CANONICAL_SYSTEM_PROMPT = (
    "You are the AI Platform for Lots Lots More. "
    "You help employees work across company knowledge, workflows, documents, "
    "connected accounts, and business systems. "
    "You are not tied to one system. "
    "You may use connected tools such as Odoo, GitHub, Azure CLI, Microsoft Graph, Exchange Online, Teams Admin, SharePoint/PnP, documents, and Workspace "
    "only when they are available, authorised, and relevant. "
    "Never claim live access to a system unless that connector is connected and "
    "permitted for the current user. "
    "If a required connector is not connected, explain that clearly and guide "
    "the user to Connected Accounts. "
    "Use the provided current date and time for relative dates such as today, "
    "this month, and this year. Never ask the user to confirm today's date "
    "when that context is available. "
    "If a tool result says it was truncated or incomplete, never infer missing "
    "records from naming patterns; run a narrower tool query or state that the "
    "output is incomplete. "
    "Never invent quantitative connected-system facts such as costs, counts, or balances from prior assistant "
    "messages; use successful current tool results or clearly say what could not be verified. "
    "Keep responses practical, business-focused, and clear."
)

DEFAULT_PLATFORM_TIMEZONE = os.environ.get("PLATFORM_TIMEZONE", "Africa/Johannesburg")


@dataclass
class InjectedContext:
    system_prompt: str
    memories: list[Any] = field(default_factory=list)


@dataclass
class ModelCallStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    tool_calls: int = 0

    def add_result(self, result: dict[str, Any]) -> None:
        self.prompt_tokens += result.get("prompt_tokens", 0)
        self.completion_tokens += result.get("completion_tokens", 0)
        self.latency_ms += result.get("latency_ms", 0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ModelCallState:
    result: dict[str, Any]
    used_model: AIModel
    used_provider: AIProvider
    client: ModelProviderClient
    stats: ModelCallStats


@dataclass
class ConnectedAccountsSnapshot:
    accounts: list[AIConnectedAccount] = field(default_factory=list)

    @property
    def connected_systems(self) -> set[str]:
        return {
            account.provider
            for account in self.accounts
            if account.status in ("connected", "active")
        }

    def first_connected(self, provider: str) -> Optional[AIConnectedAccount]:
        for account in self.accounts:
            if account.provider == provider and account.status in ("connected", "active"):
                return account
        return None


class RouteNotFoundError(Exception):
    def __init__(self, task_type: str):
        self.task_type = task_type
        super().__init__(ROUTE_NOT_CONFIGURED_MESSAGE)


class ProviderCallError(Exception):
    def __init__(self, message: str, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(message)


def _platform_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(DEFAULT_PLATFORM_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid PLATFORM_TIMEZONE=%s; using UTC", DEFAULT_PLATFORM_TIMEZONE)
        return ZoneInfo("UTC")


def _platform_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(_platform_timezone())
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc).astimezone(_platform_timezone())
    return now.astimezone(_platform_timezone())


def _current_time_context(now: Optional[datetime] = None) -> str:
    local_now = _platform_now(now)
    utc_now = local_now.astimezone(timezone.utc)
    return (
        "## Current Date and Time\n"
        f"- Current date: {local_now.date().isoformat()}\n"
        f"- Current local time: {local_now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"- Timezone: {local_now.tzinfo.key if hasattr(local_now.tzinfo, 'key') else str(local_now.tzinfo)}\n"
        f"- Current UTC time: {utc_now.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        "Use this for relative periods. For example, this month starts on "
        f"{local_now.replace(day=1).date().isoformat()} and ends today, "
        f"{local_now.date().isoformat()}, unless the user asks for a full calendar month."
    )


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


async def _resolve_api_key(provider: AIProvider) -> str:
    """Resolve a provider API key from the configured provider secret."""
    if provider.auth_type != "key_vault_secret" or not provider.secret_reference:
        raise RuntimeError(f"Provider {provider.name} does not have a configured API key secret.")
    try:
        secret_value = await get_secret_value(provider.secret_reference)
    except Exception as exc:
        raise RuntimeError(f"Failed to read API key secret for provider {provider.name}: {exc}") from exc
    if not secret_value:
        raise RuntimeError(f"API key secret for provider {provider.name} is empty.")
    return secret_value


async def build_model_client(provider: AIProvider, model: AIModel) -> ModelProviderClient:
    api_key = await _resolve_api_key(provider)
    config = model.config_json if isinstance(model.config_json, dict) else {}
    return ModelProviderClient(
        base_url=provider.base_url,
        deployment_name=model.deployment_name,
        api_key=api_key,
        request_options=config.get("request_options") if isinstance(config.get("request_options"), dict) else None,
    )


KNOWN_CONNECTOR_TYPES = ["odoo", *MICROSOFT_NATIVE_CONNECTOR_SYSTEMS, "github"]

CONNECTOR_DISPLAY_NAMES: dict[str, str] = {
    "odoo": "Odoo",
    "azure_cli": "Azure CLI",
    "microsoft_graph": "Microsoft Graph",
    "exchange_online": "Exchange Online",
    "teams_admin": "Teams Admin",
    "sharepoint_pnp": "SharePoint / PnP",
    "github": "GitHub",
}

ODOO_CONNECTOR_URL: str = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY: str = os.environ.get("ODOO_CONNECTOR_API_KEY", "")
DELEGATED_AUTH_FAILURE_MARKERS = (
    "does not exist in msal token cache",
    "run `az login`",
    "azure cli profile",
    "azure is not connected",
    "azure token is expired",
    "microsoft graph is not connected",
    "exchange online is not connected",
    "teams admin is not connected",
    "sharepoint is not connected",
    "microsoft delegated credentials",
)


MICROSOFT_TOOL_PROVIDER_BY_NAME = {
    "ms_azure_cli": "azure_cli",
    "ms_graph": "microsoft_graph",
    "ms_exchange_powershell": "exchange_online",
    "ms_teams_powershell": "teams_admin",
    "ms_sharepoint_pnp_powershell": "sharepoint_pnp",
}


def _truncate_connector_skill(content: str) -> str:
    if len(content) <= CONNECTOR_SKILL_MAX_CHARS:
        return content
    omitted = len(content) - CONNECTOR_SKILL_MAX_CHARS
    return content[:CONNECTOR_SKILL_MAX_CHARS].rstrip() + f"\n\n[connector skill truncated by {omitted} characters]"


def _truncate_tool_skill(content: str) -> str:
    if len(content) <= TOOL_SKILL_MAX_CHARS:
        return content
    omitted = len(content) - TOOL_SKILL_MAX_CHARS
    return content[:TOOL_SKILL_MAX_CHARS].rstrip() + f"\n\n[tool skill truncated by {omitted} characters]"


def _selected_tool_names(tools: list[AITool]) -> set[str]:
    return {str(tool.name or "") for tool in tools}


def _selected_connector_skill_systems(connected_systems: set[str], tools: list[AITool]) -> list[str]:
    tool_names = _selected_tool_names(tools)
    if WORKSPACE_TOOL_NAME not in tool_names and "odoo" not in tool_names:
        return []
    systems: list[str] = []
    if "odoo" in connected_systems:
        systems.append("odoo")
    return systems


async def _fetch_odoo_connector_skill() -> str | None:
    if not ODOO_CONNECTOR_URL:
        return None
    url = f"{ODOO_CONNECTOR_URL.rstrip('/')}/odoo/guidance"
    headers = {"X-Internal-API-Key": ODOO_CONNECTOR_KEY}
    async with httpx.AsyncClient(timeout=CONNECTOR_SKILL_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=headers)
    if response.status_code >= 400:
        logger.warning("Odoo connector skill fetch failed with status %s", response.status_code)
        return None
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    version = str(payload.get("version") or "unknown")
    source = str(payload.get("source") or "connector package")
    return (
        "### Odoo Connector Skill\n"
        f"Version: {version}\n"
        f"Source: {source}\n\n"
        f"{_truncate_connector_skill(content)}"
    )


async def _connector_skill_context(connected_systems: set[str], tools: list[AITool]) -> str:
    systems = _selected_connector_skill_systems(connected_systems, tools)
    if not systems:
        return ""

    sections: list[str] = []
    for system in systems:
        try:
            skill = await _fetch_odoo_connector_skill() if system == "odoo" else None
        except Exception as exc:
            logger.warning("Failed to fetch %s connector skill: %s", system, exc)
            skill = None
        if skill:
            sections.append(skill)

    if not sections:
        return ""
    return (
        "## Connector Skills\n"
        "The following skill text is owned by the connector package. Use it with Workspace and the connector broker target; "
        "do not invent connector-specific API flows when the skill gives the raw method flow.\n\n"
        + "\n\n".join(sections)
    )


def _tool_skill_context(tools: list[AITool]) -> str:
    sections: list[str] = []
    for tool in tools:
        name = str(tool.name or "")
        content = tool_skill_markdown(name)
        if not content:
            continue
        sections.append(
            f"### {tool.display_name or name} Tool Skill\n"
            f"Source: app/tools/{name}/SKILL.md\n\n"
            f"{_truncate_tool_skill(content)}"
        )
    if not sections:
        return ""
    return (
        "## Tool Skills\n"
        "The following skill text is owned by selected platform tools. Use it with the tool directly or from Workspace; "
        "do not replace it with ad hoc prompt rules.\n\n"
        + "\n\n".join(sections)
    )


def _connector_error_payload(raw_detail: Any, default_message: str = "") -> dict[str, Any]:
    detail = raw_detail.get("detail") if isinstance(raw_detail, dict) and "detail" in raw_detail else raw_detail
    if not isinstance(detail, dict):
        message = str(detail or default_message or "Connector returned an error.")
        return {
            "error_type": "connector_http_error",
            "message": _truncate_tool_text(message, 1200),
        }

    error_type = str(detail.get("error_type") or detail.get("error") or "connector_error")
    raw_message = detail.get("message") or detail.get("detail") or default_message or error_type
    message = json.dumps(raw_message, ensure_ascii=False, default=str) if isinstance(raw_message, (dict, list)) else str(raw_message)

    safe: dict[str, Any] = {
        "error_type": error_type,
        "message": _truncate_tool_text(message, 1200),
    }
    for key in ("model", "field", "suggestion", "correlation_id", "status_code"):
        if key in detail and detail[key] not in (None, ""):
            safe[key] = detail[key]
    return safe


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
    if account.secret_reference and key_vault_uri():
        try:
            api_key = await get_secret_value(account.secret_reference)
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve Odoo credentials from Key Vault: {e}")

    if not api_key:
        raise RuntimeError("Odoo connected account has no valid credentials")

    odoo_url = (account.odoo_url or "").strip()
    odoo_db = (account.odoo_db or "").strip()
    if not odoo_url or not odoo_db:
        raise RuntimeError("Odoo connected account is missing its saved URL or database")

    logger.info("Resolved Odoo credentials for tool execution: user=%s host=%s db=%s",
                account.provider_username, odoo_url, odoo_db)

    return {
        "url": odoo_url,
        "db": odoo_db,
        "username": account.provider_username or "",
        "api_key": api_key,
    }


async def _execute_document_reader_tool(
    db: AsyncSession,
    user_id: UUID,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Return read-only extraction status or text for one uploaded artifact."""
    if not user_id:
        return {
            "error": True,
            "status": "failed",
            "error_type": "authentication_required",
            "message": "Document Reader requires an authenticated user.",
        }

    mode = str(arguments.get("mode") or "read").strip().lower()
    if mode == "guidance":
        payload = tool_guidance_payload("document_reader")
        if payload:
            return {"status": "success", "tool_name": "document_reader", "mode": "guidance", **payload}
        return {
            "error": True,
            "status": "failed",
            "tool_name": "document_reader",
            "mode": "guidance",
            "error_type": "guidance_not_found",
            "message": "Document Reader guidance was not found.",
        }

    raw_artifact_id = str(arguments.get("artifact_id") or "").strip()
    if not raw_artifact_id:
        return {
            "error": True,
            "status": "failed",
            "error_type": "invalid_tool_arguments",
            "missing": ["artifact_id"],
            "message": "Provide artifact_id for Document Reader.",
        }
    try:
        artifact_id = UUID(raw_artifact_id)
    except (TypeError, ValueError):
        return {
            "error": True,
            "status": "failed",
            "error_type": "invalid_tool_arguments",
            "message": "artifact_id must be a valid UUID.",
        }

    if mode not in {"status", "preview", "extract", "read", "tables", "layout"}:
        return {
            "error": True,
            "status": "failed",
            "error_type": "invalid_tool_arguments",
            "message": "mode must be one of: guidance, status, preview, extract, read, tables, layout.",
        }

    try:
        max_chars = int(arguments.get("max_chars") or 12000)
    except (TypeError, ValueError):
        max_chars = 12000
    max_chars = max(1000, min(max_chars, DOCUMENT_READER_MAX_CHARS))

    result = await db.execute(
        select(AIArtifact).where(
            AIArtifact.id == artifact_id,
            or_(AIArtifact.created_by_user_id == user_id, AIArtifact.created_by_user_id.is_(None)),
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        return {
            "error": True,
            "status": "failed",
            "error_type": "not_found",
            "message": "Uploaded artifact was not found for this user.",
        }

    def metadata_summary(metadata: Any) -> Any:
        if not isinstance(metadata, dict):
            return metadata
        summary = {key: value for key, value in metadata.items() if key != "layout"}
        layout = metadata.get("layout")
        if isinstance(layout, dict):
            summary["layout"] = {
                "page_count": layout.get("page_count"),
                "table_count": layout.get("table_count"),
                "stored_table_count": len(layout.get("tables") or []),
                "lines_truncated": layout.get("lines_truncated"),
                "tables_truncated": layout.get("tables_truncated"),
            }
        return summary

    def layout_from_artifact() -> dict[str, Any]:
        metadata = getattr(artifact, "extraction_metadata_json", None)
        if not isinstance(metadata, dict):
            return {}
        layout = metadata.get("layout")
        return layout if isinstance(layout, dict) else {}

    payload: dict[str, Any] = {
        "status": "success",
        "tool_name": "document_reader",
        "artifact_id": str(artifact.id),
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "extraction_status": getattr(artifact, "extraction_status", None),
        "extraction_source": getattr(artifact, "extraction_source", None),
        "extraction_metadata": metadata_summary(getattr(artifact, "extraction_metadata_json", None)),
        "extraction_error": getattr(artifact, "extraction_error", None),
    }
    if mode == "status":
        return payload

    from app.services.artifact import ArtifactService

    artifact_svc = ArtifactService(db)
    if mode == "read":
        try:
            offset = int(arguments.get("offset") or 1)
        except (TypeError, ValueError):
            offset = 1
        try:
            limit = int(arguments.get("limit") or DOCUMENT_READER_DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = DOCUMENT_READER_DEFAULT_LIMIT
        offset = max(1, offset)
        limit = max(1, min(limit, DOCUMENT_READER_MAX_LIMIT))

        text = await artifact_svc.readable_text(artifact)
        lines = (text or "").splitlines()
        total_lines = len(lines)
        end_line = min(total_lines, offset + limit - 1)
        selected = lines[offset - 1:end_line] if offset <= total_lines else []
        content = "\n".join(
            f"{line_number}|{line}"
            for line_number, line in enumerate(selected, start=offset)
        )
        truncated = end_line < total_lines
        payload.update(
            {
                "mode": "read",
                "content": content,
                "offset": offset,
                "limit": limit,
                "total_lines": total_lines,
                "character_count": len(text or ""),
                "truncated": truncated,
            }
        )
        if truncated:
            payload["next_offset"] = end_line + 1
            payload["hint"] = (
                f"Use offset={end_line + 1} to continue reading "
                f"(showing {offset}-{end_line} of {total_lines} lines)."
            )
        if text is None:
            payload["message"] = "No readable text is available for this artifact."
        return payload

    if mode == "tables":
        try:
            table_offset = int(arguments.get("table_offset") or 1)
        except (TypeError, ValueError):
            table_offset = 1
        try:
            table_limit = int(arguments.get("table_limit") or DOCUMENT_READER_DEFAULT_TABLE_LIMIT)
        except (TypeError, ValueError):
            table_limit = DOCUMENT_READER_DEFAULT_TABLE_LIMIT
        table_offset = max(1, table_offset)
        table_limit = max(1, min(table_limit, DOCUMENT_READER_MAX_TABLE_LIMIT))

        text = await artifact_svc.readable_text(artifact, require_layout=True)
        layout = layout_from_artifact()
        tables = layout.get("tables") if isinstance(layout.get("tables"), list) else []
        total_tables = len(tables)
        end_index = min(total_tables, table_offset + table_limit - 1)
        selected = tables[table_offset - 1:end_index] if table_offset <= total_tables else []
        truncated = end_index < total_tables

        payload.update(
            {
                "mode": "tables",
                "extraction_status": getattr(artifact, "extraction_status", None),
                "extraction_source": getattr(artifact, "extraction_source", None),
                "extraction_metadata": metadata_summary(getattr(artifact, "extraction_metadata_json", None)),
                "extraction_error": getattr(artifact, "extraction_error", None),
                "table_offset": table_offset,
                "table_limit": table_limit,
                "total_tables": total_tables,
                "tables": selected,
                "character_count": len(text or ""),
                "truncated": truncated,
            }
        )
        if truncated:
            payload["next_table_offset"] = end_index + 1
            payload["hint"] = (
                f"Use table_offset={end_index + 1} to continue reading tables "
                f"(showing {table_offset}-{end_index} of {total_tables})."
            )
        if not selected:
            payload["message"] = (
                "No structured tables were detected for this artifact. "
                "Use mode='layout' for page lines or mode='read' for raw OCR text."
            )
        return payload

    if mode == "layout":
        try:
            page_offset = int(arguments.get("page_offset") or 1)
        except (TypeError, ValueError):
            page_offset = 1
        try:
            page_limit = int(arguments.get("page_limit") or DOCUMENT_READER_DEFAULT_PAGE_LIMIT)
        except (TypeError, ValueError):
            page_limit = DOCUMENT_READER_DEFAULT_PAGE_LIMIT
        page_offset = max(1, page_offset)
        page_limit = max(1, min(page_limit, DOCUMENT_READER_MAX_PAGE_LIMIT))

        text = await artifact_svc.readable_text(artifact, require_layout=True)
        layout = layout_from_artifact()
        pages = layout.get("pages") if isinstance(layout.get("pages"), list) else []
        total_pages = len(pages)
        end_index = min(total_pages, page_offset + page_limit - 1)
        selected = pages[page_offset - 1:end_index] if page_offset <= total_pages else []
        truncated = end_index < total_pages

        payload.update(
            {
                "mode": "layout",
                "extraction_status": getattr(artifact, "extraction_status", None),
                "extraction_source": getattr(artifact, "extraction_source", None),
                "extraction_metadata": metadata_summary(getattr(artifact, "extraction_metadata_json", None)),
                "extraction_error": getattr(artifact, "extraction_error", None),
                "page_offset": page_offset,
                "page_limit": page_limit,
                "total_pages": total_pages,
                "pages": selected,
                "table_count": layout.get("table_count", 0),
                "tables": [
                    {
                        "table_index": table.get("table_index"),
                        "row_count": table.get("row_count"),
                        "column_count": table.get("column_count"),
                        "cell_count": table.get("cell_count"),
                    }
                    for table in (layout.get("tables") or [])
                    if isinstance(table, dict)
                ],
                "character_count": len(text or ""),
                "truncated": truncated,
            }
        )
        if truncated:
            payload["next_page_offset"] = end_index + 1
            payload["hint"] = (
                f"Use page_offset={end_index + 1} to continue reading layout pages "
                f"(showing {page_offset}-{end_index} of {total_pages})."
            )
        return payload

    preview = await artifact_svc.text_preview(artifact, max_chars=max_chars)
    payload.update(
        {
            "mode": mode,
            "extraction_status": getattr(artifact, "extraction_status", None),
            "extraction_source": getattr(artifact, "extraction_source", None),
            "extraction_metadata": metadata_summary(getattr(artifact, "extraction_metadata_json", None)),
            "extraction_error": getattr(artifact, "extraction_error", None),
            "text": preview or "",
            "character_count": len(preview or ""),
            "truncated": bool(preview and len(preview) >= max_chars),
        }
    )
    return payload


async def _execute_tool_call_impl(
    db: AsyncSession,
    user_id: UUID,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    workspace_session: WorkspaceSession | None = None,
) -> dict[str, Any]:
    """Execute a tool call by routing to the appropriate connector."""
    if tool_name == WORKSPACE_TOOL_NAME:
        if workspace_session is not None:
            return await workspace_session.run(arguments)

        async def workspace_tool_executor(nested_tool_name: str, nested_arguments: dict[str, Any]) -> dict[str, Any]:
            return await _execute_tool_call_impl(db, user_id, nested_tool_name, nested_arguments)

        return await run_workspace(arguments, tool_executor=workspace_tool_executor)

    if tool_name == "document_reader":
        return await _execute_document_reader_tool(db, user_id, arguments)

    if tool_name == "odoo":
        if arguments.get("operation") == "guidance":
            url = f"{ODOO_CONNECTOR_URL.rstrip('/')}/odoo/guidance" if ODOO_CONNECTOR_URL else ""
            if not url:
                return {"error": "Odoo connector URL not configured"}
            headers = {"X-Internal-API-Key": ODOO_CONNECTOR_KEY}
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
            if response.status_code >= 400:
                try:
                    raw_detail = response.json()
                except Exception:
                    raw_detail = {"error_type": "connector_http_error", "message": response.text}
                detail = _connector_error_payload(raw_detail, response.text)
                return {
                    "error": True,
                    "status_code": response.status_code,
                    "connector_error": detail,
                    "error_type": detail.get("error_type") or "connector_error",
                    "message": detail.get("message") or "Connector returned an error.",
                }
            return response.json()

        credentials = await _resolve_odoo_credentials_for_tool(db, user_id)
        payload = {
            "credentials": credentials,
            "identity_mode": "user-delegated",
            **arguments,
        }
        path = "/odoo/orm/run"
        url = f"{ODOO_CONNECTOR_URL.rstrip('/')}{path}" if ODOO_CONNECTOR_URL else ""
        if not url:
            return {"error": "Odoo connector URL not configured"}
        headers = {"X-Internal-API-Key": ODOO_CONNECTOR_KEY, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            try:
                raw_detail = response.json()
            except Exception:
                raw_detail = {"error_type": "connector_http_error", "message": response.text}
            detail = _connector_error_payload(raw_detail, response.text)
            return {
                "error": True,
                "status_code": response.status_code,
                "connector_error": detail,
                "error_type": detail.get("error_type") or "connector_error",
                "message": detail.get("message") or "Connector returned an error.",
            }
        result = response.json()
        return result

    if tool_name in MICROSOFT_NATIVE_TOOL_NAMES or tool_name == "github_cli":
        from app.services.connectors.github_cli import run_github_cli_command
        from app.services.connectors.microsoft_admin.azure_cli import run_ms_azure_cli_tool
        from app.services.connectors.microsoft_admin.graph import run_ms_graph_tool
        from app.services.connectors.microsoft_admin.powershell_exchange import run_ms_exchange_powershell_tool
        from app.services.connectors.microsoft_admin.powershell_pnp import run_ms_sharepoint_pnp_powershell_tool
        from app.services.connectors.microsoft_admin.powershell_teams import run_ms_teams_powershell_tool

        command = str(arguments.get("command", ""))
        timeout = int(arguments.get("timeout", 60))
        if tool_name == "ms_azure_cli":
            return await run_ms_azure_cli_tool(arguments, user_id, timeout=timeout)
        if tool_name == "ms_graph":
            return await run_ms_graph_tool(arguments, user_id, timeout=timeout)
        if tool_name == "ms_exchange_powershell":
            return await run_ms_exchange_powershell_tool(arguments, user_id, timeout=timeout)
        if tool_name == "ms_teams_powershell":
            return await run_ms_teams_powershell_tool(arguments, user_id, timeout=timeout)
        if tool_name == "ms_sharepoint_pnp_powershell":
            return await run_ms_sharepoint_pnp_powershell_tool(arguments, user_id, timeout=timeout)
        return await run_github_cli_command(command, user_id, timeout=timeout)

    return {
            "status": "failed",
            "error": f"Unknown tool: {tool_name}",
            "error_type": "unknown_tool",
            "message": (
                "This tool name is not part of the current tool registry. "
                "Use the registered native Microsoft tool surfaces."
            ),
        }


async def _execute_tool_call(
    db: AsyncSession,
    user_id: UUID,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    workspace_session: WorkspaceSession | None = None,
    trace_svc: Any = None,
) -> dict[str, Any]:
    """Execute a tool call and record a troubleshooting span when tracing is enabled."""
    span_id = None
    if trace_svc:
        span_id = trace_svc.start_span(
            "tool_call",
            tool_name,
            input_summary={
                "tool_name": tool_name,
                "user_id": str(user_id) if user_id else None,
                "arguments": arguments,
            },
        )
    try:
        result = await _execute_tool_call_impl(db, user_id, tool_name, arguments, workspace_session=workspace_session)
    except Exception as exc:
        if trace_svc and span_id:
            trace_svc.span_error(span_id, type(exc).__name__, str(exc))
        raise

    if trace_svc and span_id:
        has_result_error = isinstance(result, dict) and bool(result.get("error") or result.get("status") == "failed")
        span_status = "success"
        if has_result_error:
            span_status = "failed"
        error_type = result.get("error_type") if isinstance(result, dict) else None
        error_message = (result.get("message") or result.get("error")) if isinstance(result, dict) else None
        trace_svc.end_span(
            span_id,
            status=span_status,
            output_summary={"result": result},
            error_type=error_type if has_result_error else None,
            error_message=str(error_message) if has_result_error and error_message else None,
        )
    return result


async def _record_delegated_tool_auth_failure(
    db: AsyncSession,
    user_id: Optional[UUID],
    tool_name: str,
    result: dict[str, Any],
) -> None:
    if not user_id or tool_name not in MICROSOFT_NATIVE_TOOL_NAMES or result.get("status") != "failed":
        return

    message = " ".join(
        str(result.get(key) or "")
        for key in ("error", "message", "stderr")
    ).strip()
    lower_message = message.lower()
    if not any(marker in lower_message for marker in DELEGATED_AUTH_FAILURE_MARKERS):
        return

    provider = MICROSOFT_TOOL_PROVIDER_BY_NAME.get(tool_name)
    if not provider:
        return

    status = "expired" if "expired" in lower_message else "error"
    await upsert_delegated_account(
        db,
        provider,
        user_id,
        status=status,
        permission_summary=message[:500] if message else "Native Microsoft delegated credentials are not usable.",
    )

def _truncate_tool_text(value: str, limit: int = MAX_TOOL_RESULT_STRING_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... [truncated {len(value) - limit} chars]"


def _compact_stdio_text(value: str) -> str | dict[str, Any]:
    if len(value) <= MAX_TOOL_STDIO_STRING_CHARS:
        return value
    return {
        "truncated": True,
        "chars": len(value),
        "preview": value[:MAX_TOOL_STDIO_STRING_CHARS],
        "warning": (
            "Output is incomplete. Do not infer missing rows or values from this preview; "
            "run a narrower command or explain that the tool output was truncated."
        ),
    }


def _compact_tool_value(value: Any, key: str = "", depth: int = 0) -> Any:
    """Return a model-safe summary of connector output.

    This is not an authorization layer. It keeps provider data usable while
    preventing one large connector response from becoming a huge follow-up
    prompt or persisted chat payload.
    """
    key_lower = key.lower()
    if depth > 8:
        return {"truncated": True, "reason": "max_depth"}

    if any(sensitive in key_lower for sensitive in ("password", "secret", "token", "api_key", "authorization", "cookie")):
        return {"redacted": True}

    if key_lower in OMITTED_TOOL_CONTENT_KEYS:
        if isinstance(value, str):
            return {"omitted": True, "chars": len(value)}
        return {"omitted": True}

    if isinstance(value, str):
        if key_lower in ("stdout", "stderr"):
            return _compact_stdio_text(value)
        if key_lower in LARGE_TEXT_TOOL_KEYS:
            return _truncate_tool_text(value)
        return _truncate_tool_text(value)

    if isinstance(value, list):
        item_limit = (
            MAX_TOOL_RESULT_RECORD_ITEMS
            if key_lower in {"records", "result", "lines", "groups", "value"}
            else MAX_TOOL_RESULT_LIST_ITEMS
        )
        compact_items = [_compact_tool_value(item, key, depth + 1) for item in value[:item_limit]]
        if len(value) <= item_limit:
            return compact_items
        return {
            "items": compact_items,
            "total_items": len(value),
            "truncated_items": len(value) - item_limit,
        }

    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        items = list(value.items())
        for child_key, child_value in items[:MAX_TOOL_RESULT_DICT_KEYS]:
            compact[child_key] = _compact_tool_value(child_value, child_key, depth + 1)
        if len(items) > MAX_TOOL_RESULT_DICT_KEYS:
            compact["_truncated_keys"] = len(items) - MAX_TOOL_RESULT_DICT_KEYS
        return compact

    return value


def _compact_document_reader_result(result: dict[str, Any]) -> dict[str, Any]:
    """Preserve document read output like Hermes preserves read_file output.

    The generic connector compactor intentionally crushes ordinary string
    fields to small previews. File/document reads are different: their purpose
    is to place bounded text in the tool context so the model can reason over
    it, then page forward with offset/limit when more is needed.
    """
    document_list_limits = {
        "tables": DOCUMENT_READER_MAX_TABLE_LIMIT,
        "pages": DOCUMENT_READER_MAX_PAGE_LIMIT,
        "rows": 1000,
        "cells": 5000,
        "values": 200,
    }

    def compact_document_value(value: Any, key: str = "", depth: int = 0) -> Any:
        if depth > 10:
            return {"truncated": True, "reason": "max_depth"}

        if isinstance(value, str):
            if key in {"content", "text"}:
                return _truncate_tool_text(value, DOCUMENT_READER_MAX_CHARS)
            if key == "markdown":
                return _truncate_tool_text(value, DOCUMENT_READER_MAX_CHARS)
            return _truncate_tool_text(value)

        if isinstance(value, list):
            key_lower = key.lower()
            item_limit = document_list_limits.get(key_lower, MAX_TOOL_RESULT_LIST_ITEMS)
            compact_items = [
                compact_document_value(item, key_lower, depth + 1)
                for item in value[:item_limit]
            ]
            if len(value) <= item_limit:
                return compact_items
            return {
                "items": compact_items,
                "total_items": len(value),
                "truncated_items": len(value) - item_limit,
            }

        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            items = list(value.items())
            for child_key, child_value in items[:MAX_TOOL_RESULT_DICT_KEYS]:
                compact[child_key] = compact_document_value(child_value, child_key, depth + 1)
            if len(items) > MAX_TOOL_RESULT_DICT_KEYS:
                compact["_truncated_keys"] = len(items) - MAX_TOOL_RESULT_DICT_KEYS
            return compact

        return value

    compact: dict[str, Any] = {}
    for key, value in result.items():
        if key in {"content", "text"} and isinstance(value, str):
            compact[key] = _truncate_tool_text(value, DOCUMENT_READER_MAX_CHARS)
            if len(value) > DOCUMENT_READER_MAX_CHARS:
                compact[f"{key}_truncated"] = True
                compact[f"{key}_chars"] = len(value)
                compact[f"{key}_hint"] = "Use document_reader mode='read' with offset and limit to read a narrower page."
            continue
        compact[key] = compact_document_value(value, key, 1)
    return json.loads(json.dumps(compact, ensure_ascii=False, default=str))


def _compact_tool_result_for_model(result: Any) -> Any:
    if isinstance(result, dict) and result.get("tool_name") == "document_reader":
        return _compact_document_reader_result(result)

    compacted = _compact_tool_value(result)
    payload = json.dumps(compacted, ensure_ascii=False, default=str)
    serializable = json.loads(payload)
    if len(payload) <= MAX_TOOL_RESULT_JSON_CHARS:
        return serializable
    return {
        "summary": "Tool result was too large and was compacted before model follow-up.",
        "result_preview": _truncate_tool_text(payload, MAX_TOOL_RESULT_JSON_CHARS),
        "original_compacted_chars": len(payload),
    }


def _tool_message_content(compacted_result: Any) -> str:
    return json.dumps(compacted_result, ensure_ascii=False, default=str)


def _safe_tool_error_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "command", "query", "model", "operation", "method", "resource",
        "timeout", "fields", "limit", "order", "language", "purpose",
    }
    safe: dict[str, Any] = {}
    for key in allowed:
        if key not in arguments or arguments[key] in (None, ""):
            continue
        value = arguments[key]
        if isinstance(value, str):
            safe[key] = _truncate_tool_text(value, 180)
        elif isinstance(value, list):
            safe[key] = value[:8]
        elif isinstance(value, dict):
            safe[key] = sorted(value.keys())[:8]
        else:
            safe[key] = value
    return safe or {"argument_keys": sorted(arguments.keys())[:8]}


def _tool_result_error_summary(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for index, tool_result in enumerate(tool_results, start=1):
        result = tool_result.get("result")
        if not isinstance(result, dict):
            continue

        result_status = str(result.get("status") or "").strip().lower()
        has_error = bool(result.get("error") or result_status == "failed")
        if not has_error:
            continue

        arguments = tool_result.get("arguments") if isinstance(tool_result.get("arguments"), dict) else {}
        message = str(result.get("message") or result.get("error") or result.get("error_type") or "Tool returned an error.")
        summary.append({
            "index": index,
            "tool_name": tool_result.get("tool_name"),
            "status": result_status or ("failed" if has_error else "unknown"),
            "error_type": str(result.get("error_type") or "tool_error"),
            "message": _truncate_tool_text(message, 500),
            "arguments": _safe_tool_error_arguments(arguments),
        })
        if len(summary) >= TOOL_ERROR_SUMMARY_LIMIT:
            break
    return summary


def _workspace_generated_files(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or "workspace_id" not in result:
        return []
    input_paths = {
        str(item.get("path") or "")
        for item in result.get("input_files") or []
        if isinstance(item, dict)
    }
    generated: list[dict[str, Any]] = []
    for item in result.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        content_base64 = item.get("content_base64")
        if not path or path in input_paths or not isinstance(content_base64, str) or not content_base64:
            continue
        generated.append({
            "path": path,
            "filename": path.rsplit("/", 1)[-1],
            "mime_type": item.get("mime_type") or "application/octet-stream",
            "bytes": item.get("bytes"),
            "sha256": item.get("sha256"),
            "content_base64": content_base64,
            "workspace_id": result.get("workspace_id"),
            "run_index": result.get("run_index"),
        })
    return generated


def _tool_error_summary_message(tool_error_summary: list[dict[str, Any]]) -> str | None:
    if not tool_error_summary:
        return None
    parts: list[str] = []
    for item in tool_error_summary[:3]:
        tool_name = str(item.get("tool_name") or "tool")
        error_type = str(item.get("error_type") or "tool_error")
        message = _truncate_tool_text(str(item.get("message") or ""), 180)
        parts.append(f"{tool_name}: {error_type}{f' - {message}' if message else ''}")
    hidden = len(tool_error_summary) - len(parts)
    if hidden > 0:
        parts.append(f"... {hidden} more tool issue(s)")
    return "; ".join(parts)


async def _load_connected_accounts(db: AsyncSession, user_id: Optional[UUID]) -> ConnectedAccountsSnapshot:
    if not user_id:
        return ConnectedAccountsSnapshot()
    return ConnectedAccountsSnapshot(accounts=await effective_connected_accounts(db, user_id))


async def _get_connector_context(
    db: AsyncSession,
    user_id: Optional[UUID],
    snapshot: Optional[ConnectedAccountsSnapshot] = None,
) -> str:
    """Build a connector-availability context block for the current user.

    Queries AIConnectedAccount for the user and returns a human-readable
    block that the model can use to know which systems are actually available.
    """
    lines: list[str] = ["Connected Account Status:"]
    if not user_id:
        lines.append("  (no authenticated user context)")
        return "\n".join(lines)

    snapshot = snapshot or await _load_connected_accounts(db, user_id)

    conn_map: dict[str, str] = {}
    for acct in snapshot.accounts:
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
            if conn_type in MICROSOFT_NATIVE_CONNECTOR_SYSTEMS and status == "connected":
                lines.append(
                    "    Native Microsoft connector access is scoped to this connector's signed-in account. "
                    "Do not claim a specific Microsoft resource is accessible until that operation succeeds. "
                    "Operations can fail because consent is missing or because the signed-in user lacks the needed "
                    "Microsoft 365 role, Azure RBAC role, Exchange role, Intune role, SharePoint permission, Teams role, or billing permission."
                )
        else:
            lines.append(f"  - {display_name}: not connected")

    return "\n".join(lines)


def _last_user_message(messages: list) -> str:
    if not messages:
        return ""
    latest = messages[-1]
    if isinstance(latest, dict):
        return str(latest.get("content") or "")
    return ""


def _tool_selection_message(messages: list) -> str:
    latest = _last_user_message(messages).strip()
    recent: list[str] = []
    for message in messages[-6:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        if role not in {"user", "assistant", "system"}:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        recent.append(f"{role}: {content[:500]}")
    if not recent:
        return latest
    return f"Latest user message:\n{latest}\n\nRecent chat context:\n" + "\n".join(recent)

async def _select_route_model_provider(
    db: AsyncSession,
    task_type: str,
) -> tuple[AIRoute, AIModel, AIProvider, dict[str, Any]]:
    route, model, provider = await get_enabled_route(db, task_type)
    return route, model, provider, {"reason": "configured_route", "requested_task_type": task_type}


def _append_tool_guidance(system_prompt: str, tools: list[AITool], tool_definitions: list[dict[str, Any]]) -> str:
    if not tool_definitions:
        return system_prompt

    available_names = [tool.name for tool in tools]
    system_prompt += (
        "\n\nYou have access to the following tools. "
        "Use a tool only when the user asks for live connected-system data, file work, code execution, "
        "calculation, or document reading. Answer ordinary conversation and questions about the previous "
        "assistant answer from the chat context without calling tools. "
        "When the previous assistant message proposed a connected-system action plan and the user approves "
        "or clarifies it, continue by calling the relevant tool in the same response. Do not only say "
        "`starting now`, `let me proceed`, or `I will do that`; either execute with the tool or state the blocker."
    )

    guidance_parts: list[str] = []
    if WORKSPACE_TOOL_NAME in available_names:
        guidance_parts.append(
            "\n\n### Workspace\n"
            "Workspace is the platform cloud-computer surface. It runs Python or shell/terminal code in a temporary "
            "directory. Python has `call(tool_name, arguments)` available by default. Shell scripts can call "
            "`ai-platform-tool <tool_name> '<json arguments>'`. Broker targets include `odoo`, `ms_azure_cli`, "
            "`ms_graph`, `ms_exchange_powershell`, `ms_teams_powershell`, `ms_sharepoint_pnp_powershell`, and "
            "`github_cli`. Calls use the user's connected accounts; those account permissions decide what succeeds. "
            "When connector-owned skill text is included in the system context, follow that skill for the connector API shape. "
            "Use Workspace when live connected-system data, code execution, terminal work, files, or calculations are "
            "needed. If a live system fact matters, check it in Workspace and answer from the tool result."
        )
    if "document_reader" in available_names:
        guidance_parts.append(
            "Documents: use `document_reader` for uploaded PDFs/images when the injected attachment or available-file "
            "preview is missing or insufficient. It is read-only; use the artifact id from the file context. "
            "The Document Reader tool owns detailed SKILL.md guidance, and `mode='guidance'` can return it. "
            "Workspace scripts can call it with `call('document_reader', {'artifact_id': id, 'mode': 'tables'})`."
        )
    return system_prompt + "\n".join(guidance_parts) if guidance_parts else system_prompt


async def _select_tools_for_model(
    db: AsyncSession,
    user_id: Optional[UUID],
    connected_systems: set[str],
    user_msg_text: str,
    task_type: str,
    risk_level: str,
    model: AIModel,
    system_prompt: str,
) -> tuple[list[AITool], list[dict[str, Any]], str]:
    if model.supports_tools != "true":
        return [], [], system_prompt

    from app.services.tool_selection import get_tool_selection

    selection = await get_tool_selection(
        db,
        user_id,
        user_msg_text,
        task_type,
        risk_level,
        connected_systems=connected_systems,
    )
    tools = selection.selected
    tool_definitions = _build_tool_definitions(tools)
    if selection.selected:
        logger.info(
            "Tool selection | intent=%s selected=%d excluded=%d schema_before=%d schema_after=%d reason=%s",
            selection.intent,
            len(selection.selected),
            len(selection.excluded),
            selection.schema_size_before,
            selection.schema_size_after,
            selection.selection_reason,
        )
    return tools, tool_definitions, _append_tool_guidance(system_prompt, tools, tool_definitions)


async def _memory_context(db: AsyncSession, user_id: Optional[UUID]) -> tuple[str, list[Any]]:
    if not user_id:
        return "", []
    mem_result = await db.execute(
        select(AIMemory).where(
            AIMemory.created_by_user_id == user_id,
            AIMemory.status == "active",
        )
        .order_by(AIMemory.priority.asc(), AIMemory.last_used_at.desc().nullslast())
        .limit(30)
    )
    memories = mem_result.scalars().all()
    if not memories:
        return "", []

    blocks: list[str] = []
    for memory in memories:
        formatted = f"- [{memory.type}] {memory.title}"
        if memory.summary:
            formatted += f": {memory.summary}"
        if memory.body:
            formatted += f"\n  Detail: {memory.body[:300]}"
        blocks.append(formatted)
    return "## Learned from Past Interactions\n" + "\n".join(blocks), memories


def _append_context_section(system_prompt: str, section: str) -> str:
    return system_prompt.rstrip() + "\n\n" + section if section else system_prompt


async def _inject_context_sections(
    db: AsyncSession,
    user_id: Optional[UUID],
    messages: list,
    system_prompt: str,
    snapshot: Optional[ConnectedAccountsSnapshot] = None,
) -> InjectedContext:
    injected = InjectedContext(system_prompt=system_prompt)

    try:
        section, injected.memories = await _memory_context(db, user_id)
        injected.system_prompt = _append_context_section(injected.system_prompt, section)
    except Exception as exc:
        logger.warning("Failed to inject memories: %s", exc)

    logger.info(
        "Context injected | memories=%d user_id=%s",
        len(injected.memories),
        user_id,
    )
    return injected


def _provider_response_summary(result: dict[str, Any]) -> dict[str, Any]:
    response = {
        "error": bool(result.get("error")),
        "content": result.get("content", ""),
        "finish_reason": result.get("finish_reason", ""),
        "tool_calls": result.get("tool_calls"),
        "model": result.get("model"),
        "raw_response": result.get("raw_response"),
    }
    if result.get("error"):
        response.update({
            "error_type": result.get("error_type"),
            "status_code": result.get("status_code"),
            "message": result.get("message"),
        })
    return {
        "response": response,
        "usage": {
            "prompt_tokens": result.get("prompt_tokens", 0),
            "completion_tokens": result.get("completion_tokens", 0),
            "total_tokens": result.get("total_tokens", 0),
        },
        "latency_ms": result.get("latency_ms", 0),
        "content_length": len(result.get("content") or ""),
        "tool_call_count": len(result.get("tool_calls") or []),
    }


def _discard_provider_stream_event(_event: dict[str, Any]) -> None:
    """Consume provider streaming when the caller does not need live turn events."""
    return None


def _agent_stream_event(
    event: dict[str, Any],
    *,
    provider: AIProvider,
    model: AIModel,
    attempt_reason: str,
) -> dict[str, Any] | None:
    delta = event.get("delta")
    if not isinstance(delta, str) or not delta:
        return None
    event_type = event.get("type")
    if event_type in {"reasoning_delta", "thinking_delta"}:
        return {
            "type": "reasoning.delta",
            "provider": provider.name,
            "model": model.display_name,
            "attempt_reason": attempt_reason,
            "text": delta,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    if event_type == "content_delta":
        return {
            "type": "message.delta",
            "provider": provider.name,
            "model": model.display_name,
            "attempt_reason": attempt_reason,
            "text": delta,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    return None


def _json_safe_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def _assistant_tool_call_message(result: dict[str, Any]) -> dict[str, Any]:
    provider_message = result.get("assistant_message")
    if isinstance(provider_message, dict):
        message = _json_safe_copy(provider_message)
        if not isinstance(message, dict):
            message = {}
        message["role"] = "assistant"
        if "content" not in message:
            message["content"] = result.get("content") or None
        if "tool_calls" not in message:
            message["tool_calls"] = _json_safe_copy(result.get("tool_calls") or [])
        return message

    message = {
        "role": "assistant",
        "content": result.get("content") or None,
        "tool_calls": _json_safe_copy(result.get("tool_calls") or []),
    }
    reasoning_content = result.get("reasoning_content")
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    return message


async def _call_model(
    model: AIModel,
    provider: AIProvider,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    tool_definitions: list[dict[str, Any]],
    trace_svc: Any = None,
    attempt_reason: str = "chat",
    client: Optional[ModelProviderClient] = None,
    stream_event_sink: Optional[Any] = None,
) -> tuple[dict[str, Any], ModelProviderClient]:
    span_id = None
    if trace_svc:
        span_id = trace_svc.start_span(
            "provider_call",
            f"{provider.name}: {model.display_name}",
            input_summary={
                "attempt_reason": attempt_reason,
                "provider": provider.name,
                "provider_type": provider.provider_type,
                "base_url": provider.base_url,
                "model": {
                    "display_name": model.display_name,
                    "model_name": model.model_name,
                    "deployment_name": model.deployment_name,
                    "supports_tools": model.supports_tools,
                    "context_window": model.context_window,
                },
                "request": {
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "tools": tool_definitions if tool_definitions else None,
                },
                "message_count": len(messages),
                "tool_count": len(tool_definitions),
            },
            metadata={
                "provider_id": str(provider.id),
                "model_id": str(model.id),
                "attempt_reason": attempt_reason,
            },
        )
    try:
        if client is None:
            client = await build_model_client(provider, model)

        streamed_reasoning = False

        def forward_stream_event(event: dict[str, Any]) -> None:
            nonlocal streamed_reasoning
            agent_event = _agent_stream_event(
                event,
                provider=provider,
                model=model,
                attempt_reason=attempt_reason,
            )
            if agent_event and stream_event_sink:
                if agent_event.get("type") == "reasoning.delta":
                    streamed_reasoning = True
                stream_event_sink(agent_event)

        result = await client.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tool_definitions if tool_definitions else None,
            stream_event_sink=forward_stream_event if stream_event_sink else _discard_provider_stream_event,
        )
        if stream_event_sink and not streamed_reasoning:
            reasoning_text = result.get("reasoning_content")
            if isinstance(reasoning_text, str) and reasoning_text.strip():
                stream_event_sink({
                    "type": "reasoning.available",
                    "provider": provider.name,
                    "model": model.display_name,
                    "attempt_reason": attempt_reason,
                    "text": reasoning_text,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
    except Exception as exc:
        if trace_svc and span_id:
            trace_svc.span_error(span_id, type(exc).__name__, str(exc))
        raise

    if trace_svc and span_id:
        failed = bool(result.get("error"))
        trace_svc.end_span(
            span_id,
            status="failed" if failed else "success",
            output_summary=_provider_response_summary(result),
            error_type=result.get("error_type") if failed else None,
            error_message=result.get("message") if failed else None,
        )
    return result, client


async def _run_model_once(
    model: AIModel,
    provider: AIProvider,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    tool_definitions: list[dict[str, Any]],
    trace_svc: Any = None,
    stream_event_sink: Optional[Any] = None,
) -> ModelCallState:
    stats = ModelCallStats()
    result, client = await _call_model(
        model,
        provider,
        messages,
        temperature,
        max_tokens,
        tool_definitions,
        trace_svc=trace_svc,
        attempt_reason="chat",
        stream_event_sink=stream_event_sink,
    )
    stats.add_result(result)
    return ModelCallState(result=result, used_model=model, used_provider=provider, client=client, stats=stats)


def _is_length_limited_final_response(result: dict[str, Any]) -> bool:
    return (
        not result.get("error")
        and not result.get("tool_calls")
        and str(result.get("finish_reason") or "").lower() == "length"
    )


def _merge_continued_content(existing: str, continuation: str) -> str:
    if not existing.strip():
        return continuation
    if not continuation.strip():
        return existing
    return existing + continuation


async def _complete_length_limited_tool_answer(
    state: ModelCallState,
    base_messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    trace_svc: Any = None,
    stream_event_sink: Optional[Any] = None,
) -> None:
    combined_content = str(state.result.get("content") or "")
    for _ in range(TOOL_LOOP_LENGTH_CONTINUATION_LIMIT):
        if not _is_length_limited_final_response(state.result):
            break

        if combined_content.strip():
            continuation_messages = (
                base_messages
                + [{"role": "assistant", "content": combined_content}]
                + [TOOL_LOOP_CONTINUE_LENGTH_MESSAGE]
            )
            attempt_reason = "tool_loop_continue"
        else:
            continuation_messages = base_messages + [TOOL_LOOP_BLANK_LENGTH_RETRY_MESSAGE]
            attempt_reason = "tool_loop_blank_retry"

        result, client = await _call_model(
            state.used_model,
            state.used_provider,
            continuation_messages,
            temperature,
            max_tokens,
            [],
            trace_svc=trace_svc,
            attempt_reason=attempt_reason,
            client=state.client,
            stream_event_sink=stream_event_sink,
        )
        state.client = client
        state.stats.add_result(result)

        new_content = str(result.get("content") or "")
        if new_content:
            combined_content = _merge_continued_content(combined_content, new_content)
            result["content"] = combined_content
        elif combined_content:
            result["content"] = combined_content

        state.result = result


async def _run_tool_loop(
    db: AsyncSession,
    user_id: Optional[UUID],
    state: ModelCallState,
    messages: list[dict[str, Any]],
    tools: list[AITool],
    tool_definitions: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    trace_svc: Any = None,
    stream_event_sink: Optional[Any] = None,
) -> list[dict[str, Any]]:
    tool_results: list[dict[str, Any]] = []
    generated_files: list[dict[str, Any]] = []
    workspace_session: WorkspaceSession | None = None

    async def workspace_tool_executor(nested_tool_name: str, nested_arguments: dict[str, Any]) -> dict[str, Any]:
        return await _execute_tool_call_impl(db, user_id, nested_tool_name, nested_arguments)

    async def get_workspace_session() -> WorkspaceSession:
        nonlocal workspace_session
        if workspace_session is None:
            session = WorkspaceSession(tool_executor=workspace_tool_executor)
            workspace_session = await session.__aenter__()
        return workspace_session

    exposed_tool_names = {
        str(((definition.get("function") or {}).get("name")) or "")
        for definition in tool_definitions
        if isinstance(definition, dict)
    }
    try:
        for iteration in range(MAX_TOOL_LOOP_ITERATIONS):
            if state.result.get("error"):
                break
            tool_calls = state.result.get("tool_calls")
            if not tool_calls:
                break

            state.result["tool_calls"] = tool_calls
            state.stats.tool_calls += len(tool_calls)
            messages.append(_assistant_tool_call_message(state.result))

            for call in tool_calls:
                if call.get("type") != "function":
                    continue
                function = call.get("function", {})
                name = function.get("name", "")
                try:
                    args = json.loads(function.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}

                if name not in exposed_tool_names:
                    result = {
                        "error": True,
                        "status": "failed",
                        "error_type": "unavailable_tool",
                        "message": f"Tool '{name}' is not exposed for this chat turn.",
                    }
                else:
                    active_workspace_session = await get_workspace_session() if name == WORKSPACE_TOOL_NAME else None
                    result = await _execute_tool_call(
                        db,
                        user_id,
                        name,
                        args,
                        workspace_session=active_workspace_session,
                        trace_svc=trace_svc,
                    )
                    if isinstance(result, dict):
                        await _record_delegated_tool_auth_failure(db, user_id, name, result)
                        generated_files.extend(_workspace_generated_files(result))
                compact_result = _compact_tool_result_for_model(result)
                tool_results.append({
                    "tool_call_id": call.get("id", ""),
                    "tool_name": name,
                    "arguments": args,
                    "result": compact_result,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": _tool_message_content(compact_result),
                })

            followup_messages = messages + [TOOL_LOOP_FOLLOWUP_MESSAGE]
            followup_max_tokens = max(max_tokens, TOOL_LOOP_RESPONSE_MAX_TOKENS)
            result, client = await _call_model(
                state.used_model,
                state.used_provider,
                followup_messages,
                temperature,
                followup_max_tokens,
                tool_definitions,
                trace_svc=trace_svc,
                attempt_reason="tool_loop",
                client=state.client,
                stream_event_sink=stream_event_sink,
            )
            state.result = result
            state.client = client
            state.stats.add_result(state.result)
            await _complete_length_limited_tool_answer(
                state,
                followup_messages,
                temperature,
                followup_max_tokens,
                trace_svc=trace_svc,
                stream_event_sink=stream_event_sink,
            )
        else:
            if state.result.get("tool_calls"):
                limit_messages = messages + [TOOL_LOOP_LIMIT_ANSWER_MESSAGE]
                result, client = await _call_model(
                    state.used_model,
                    state.used_provider,
                    limit_messages,
                    temperature,
                    max(max_tokens, TOOL_LOOP_RESPONSE_MAX_TOKENS),
                    [],
                    trace_svc=trace_svc,
                    attempt_reason="tool_loop_limit_answer",
                    stream_event_sink=stream_event_sink,
                )
                state.result = result
                state.client = client
                state.stats.add_result(state.result)
                await _complete_length_limited_tool_answer(
                    state,
                    limit_messages,
                    temperature,
                    max(max_tokens, TOOL_LOOP_RESPONSE_MAX_TOKENS),
                    trace_svc=trace_svc,
                    stream_event_sink=stream_event_sink,
                )
    finally:
        if workspace_session is not None:
            await workspace_session.__aexit__(None, None, None)
    state.result["generated_files"] = generated_files
    return tool_results

async def _log_usage(
    db: AsyncSession,
    route: AIRoute,
    task_type: str,
    chat_session_id: Optional[UUID],
    user_id: Optional[UUID],
    state: ModelCallState,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    tool_error_summary: Optional[list[dict[str, Any]]] = None,
) -> None:
    usage_status = "failed" if state.result.get("error") else "success"
    usage_error_message = state.result.get("message") if state.result.get("error") else None
    if usage_status == "success" and tool_error_summary:
        usage_status = "partial_failure"
        usage_error_message = _tool_error_summary_message(tool_error_summary)

    db.add(AIUsageLog(
        request_id=request_id,
        trace_id=trace_id,
        provider_id=state.used_provider.id,
        model_id=state.used_model.id,
        route_id=route.id,
        task_type=task_type,
        chat_session_id=chat_session_id,
        user_id=user_id,
        prompt_tokens=state.stats.prompt_tokens,
        completion_tokens=state.stats.completion_tokens,
        total_tokens=state.stats.total_tokens,
        latency_ms=state.stats.latency_ms,
        status=usage_status,
        error_message=usage_error_message,
    ))
    await db.flush()


def _provider_error_message(
    result: dict[str, Any],
    primary_model_display: str,
) -> str:
    error_type = result.get("error_type", "unknown")
    if error_type in ("rate_limit_exceeded", "quota_exceeded"):
        return (
            "The AI service is temporarily unavailable because the model "
            "quota or rate limit has been reached. "
            f"Model: {primary_model_display}. "
            "Please try again shortly, or contact support if this continues."
        )

    return {
        "authentication_error": "The AI service is unavailable due to an authentication issue. Please contact support.",
        "authorization_error": "The AI service is unavailable due to an authorization issue. Please contact support.",
        "model_not_found": "The configured AI model could not be found. Please contact support.",
        "server_error": "The AI service is temporarily unavailable. Please try again shortly, or contact support if this continues.",
        "bad_request": "The AI service received an invalid request. Please try again, or contact support if this continues.",
    }.get(error_type, "The AI service is temporarily unavailable. Please try again shortly, or contact support if this continues.")


def _raise_if_provider_failed(
    state: ModelCallState,
    primary_model: AIModel,
    primary_provider: AIProvider,
    user_id: Optional[UUID],
    chat_session_id: Optional[UUID],
    tools_enabled: bool,
) -> None:
    if not state.result.get("error"):
        return

    error_type = state.result.get("error_type", "unknown")
    raw_message = state.result.get("message", "Provider returned an error")
    status_code = state.result.get("status_code", 0)
    logger.error(
        "Provider call failed | primary_model=%s primary_provider=%s used_model=%s used_provider=%s "
        "error_type=%s status_code=%s raw_message=%s "
        "user_id=%s chat_session_id=%s tools_enabled=%s",
        primary_model.display_name,
        primary_provider.name,
        state.used_model.display_name,
        state.used_provider.name,
        error_type,
        status_code,
        raw_message,
        user_id,
        chat_session_id,
        tools_enabled,
    )
    failed_model_display = state.used_model.display_name or primary_model.display_name
    raise ProviderCallError(
        _provider_error_message(state.result, failed_model_display),
        state.used_provider.name,
        state.used_model.display_name,
    )


def _context_metadata(injected: InjectedContext, state: ModelCallState, policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "memories_injected": [{"id": str(memory.id), "title": memory.title, "type": memory.type} for memory in injected.memories],
        "current_date": _platform_now().date().isoformat(),
        "model": {
            "route": "general_chat",
            "model": state.used_model.display_name,
            "provider": state.used_provider.name,
            "routing_reason": policy.get("reason", "unknown"),
        },
    }

async def execute_chat(
    db: AsyncSession,
    messages: list,
    task_type: str = "general_chat",
    chat_session_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    trace_svc: Any = None,
    request_id: Optional[str] = None,
    stream_event_sink: Optional[Any] = None,
) -> dict:
    user_msg_text = _last_user_message(messages)
    context_span = None
    if trace_svc:
        context_span = trace_svc.start_span(
            "context_build",
            "Build chat context",
            input_summary={
                "task_type": task_type,
                "request_id": request_id,
                "user_id": str(user_id) if user_id else None,
                "chat_session_id": str(chat_session_id) if chat_session_id else None,
                "user_message": user_msg_text,
                "message_count": len(messages),
            },
        )
    try:
        risk_level = "low"
        route, model_obj, provider, policy = await _select_route_model_provider(db, task_type)
        connected_accounts = await _load_connected_accounts(db, user_id)

        system_prompt = route.system_prompt or ""
        system_prompt = _append_context_section(system_prompt, _current_time_context())
        connector_context = await _get_connector_context(db, user_id, connected_accounts)
        if connector_context:
            system_prompt = system_prompt.rstrip() + "\n\n" + connector_context

        tools, tool_definitions, system_prompt = await _select_tools_for_model(
            db,
            user_id,
            connected_accounts.connected_systems,
            _tool_selection_message(messages),
            task_type,
            risk_level,
            model_obj,
            system_prompt,
        )
        connector_skill_context = await _connector_skill_context(connected_accounts.connected_systems, tools)
        system_prompt = _append_context_section(system_prompt, connector_skill_context)
        system_prompt = _append_context_section(system_prompt, _tool_skill_context(tools))
        injected = await _inject_context_sections(db, user_id, messages, system_prompt, connected_accounts)
        full_messages = [{"role": "system", "content": injected.system_prompt}] + messages if injected.system_prompt else messages
        temperature = float(route.temperature) if route.temperature is not None else 0.3
        max_tokens = route.max_tokens or 2000
        if trace_svc and context_span:
            trace_svc.end_span(
                context_span,
                output_summary={
                    "risk_level": risk_level,
                    "route_id": str(route.id),
                    "selected_model": model_obj.display_name,
                    "selected_provider": provider.name,
                    "connected_systems": sorted(connected_accounts.connected_systems),
                    "tools": [tool.name for tool in tools],
                    "tool_count": len(tools),
                    "memories_injected": len(injected.memories),
                    "system_prompt_chars": len(injected.system_prompt or ""),
                    "full_message_count": len(full_messages),
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
    except Exception as exc:
        if trace_svc and context_span:
            trace_svc.span_error(context_span, type(exc).__name__, str(exc))
        raise

    state = await _run_model_once(
        model_obj, provider, full_messages, temperature, max_tokens, tool_definitions,
        trace_svc=trace_svc,
        stream_event_sink=stream_event_sink,
    )
    tool_results: list[dict[str, Any]] = []
    tool_results.extend(await _run_tool_loop(
        db, user_id, state, full_messages, tools, tool_definitions, temperature, max_tokens,
        trace_svc=trace_svc,
        stream_event_sink=stream_event_sink,
    ))
    tool_error_summary = _tool_result_error_summary(tool_results)
    await _log_usage(
        db,
        route,
        task_type,
        chat_session_id,
        user_id,
        state,
        request_id=request_id,
        trace_id=trace_svc.trace_id if trace_svc else None,
        tool_error_summary=tool_error_summary,
    )
    _raise_if_provider_failed(state, model_obj, provider, user_id, chat_session_id, bool(tool_definitions))
    response = {
        "content": str(state.result.get("content") or ""),
        "finish_reason": state.result.get("finish_reason", ""),
        "model_provider": state.used_provider.name,
        "model_name": state.used_model.display_name,
        "prompt_tokens": state.stats.prompt_tokens,
        "completion_tokens": state.stats.completion_tokens,
        "total_tokens": state.stats.total_tokens,
        "latency_ms": state.stats.latency_ms,
        "tool_calls": tool_results if tool_results else None,
        "generated_files": state.result.get("generated_files") or None,
        "tool_error_summary": tool_error_summary if tool_error_summary else None,
        "has_tool_errors": bool(tool_error_summary),
        "context": _context_metadata(injected, state, policy),
        "tool_call_count": state.stats.tool_calls,
    }
    return response
