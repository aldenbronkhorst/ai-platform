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
    _canonicalize_tool_call,
    _coerce_text_tool_calls,
)
from app.services.tool_registry import (
    MICROSOFT_NATIVE_CONNECTOR_SYSTEMS,
    MICROSOFT_NATIVE_TOOL_NAMES,
)
from app.services.workspace_runtime import WORKSPACE_TOOL_NAME, run_workspace

logger = logging.getLogger(__name__)

ROUTE_NOT_CONFIGURED_MESSAGE = "AI chat is not configured yet. Please ask an administrator to configure AI Providers."
MAX_TOOL_RESULT_STRING_CHARS = 600
MAX_TOOL_STDIO_STRING_CHARS = 8000
MAX_TOOL_RESULT_LIST_ITEMS = 5
MAX_TOOL_RESULT_RECORD_ITEMS = 500
MAX_TOOL_RESULT_DICT_KEYS = 60
MAX_TOOL_RESULT_JSON_CHARS = 350000
MAX_ODOO_RECORD_CONTEXT_CHARS = 300000
MAX_ODOO_RECORD_CONTEXT_ITEMS = 500
STRUCTURED_CHAT_TABLE_ROW_LIMIT = 500
TOOL_LOOP_RESPONSE_MAX_TOKENS = int(os.environ.get("TOOL_LOOP_RESPONSE_MAX_TOKENS", "24000"))
TOOL_LOOP_LENGTH_CONTINUATION_LIMIT = 3
MAX_TOOL_LOOP_ITERATIONS = int(os.environ.get("MAX_TOOL_LOOP_ITERATIONS", "10"))
TOOL_ERROR_SUMMARY_LIMIT = 8
TOOL_LOOP_FOLLOWUP_MESSAGE = {
    "role": "system",
    "content": (
        "Use the tool results already gathered to answer the user. "
        "Call another tool only when a necessary fact is still missing. "
        "If another tool is needed, call it now instead of saying you will check next. "
        "Do not tell users to run local native-tool logins; report connector auth/profile failures as platform issues. "
        "If the user asks for all rows, every record, or a complete table, include all rows visible in the tool results. "
        "Do not put final table rows in hidden reasoning. Keep non-table text concise and state any uncertainty clearly."
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
    currency_source: str = "none"
    currency_text: str | None = None


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
AZURE_FALSE_DENIAL_RE = re.compile(
    r"(?i)("
    r"\bazure(?:\s+(?:connector|account|cost\s+management))?\s*(?:is|was|[-—])\s*not\s+connected\b"
    r"|\bi\s+do\s+not\s+have\s+access\s+to\s+your\s+azure(?:\s+cost)?\s+data\b"
    r"|\byou\s+(?:would\s+)?need\s+to\s+connect\s+an\s+azure\s+account\b"
    r"|\badd/authorize\s+an\s+azure\s+connector\b"
    r")"
)
AZURE_CONNECTED_ACCESS_ERROR_MARKERS = (
    "authorizationfailed",
    "authorization failed",
    "forbidden",
    "permission",
    "permissions",
    "rbac",
    "billing",
    "access denied",
    "insufficient privileges",
    "does not have authorization",
    "not authorized",
)
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


def _handled_tool_argument_error(message: str, missing: list[str] | None = None, suggestion: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "error": True,
        "handled": True,
        "status": "skipped",
        "error_type": "invalid_tool_arguments",
        "message": message,
    }
    if missing:
        result["missing"] = missing
    if suggestion:
        result["suggestion"] = suggestion
    return result


def _validate_odoo_arguments(arguments: dict[str, Any]) -> dict[str, Any] | None:
    if "calls" in arguments:
        if isinstance(arguments.get("calls"), list):
            return None
        return _handled_tool_argument_error(
            "The Odoo raw call list must be an array.",
            missing=["calls"],
            suggestion="Retry with calls=[{model, method, args, kwargs}].",
        )
    if not arguments.get("model") or not arguments.get("method"):
        missing = [key for key in ("model", "method") if not arguments.get(key)]
        if not missing:
            missing = ["model", "method", "calls"]
        return _handled_tool_argument_error(
            "The Odoo raw call was missing model/method or calls, so it was skipped before reaching the connector.",
            missing=missing,
            suggestion="Retry with model and method for one raw call, or calls for ordered raw calls.",
        )
    return None


def _clean_odoo_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    allowed = {"model", "method", "args", "kwargs", "calls", "continue_on_error"}
    return {
        key: value
        for key, value in arguments.items()
        if key in allowed
    }


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
        "transport": "auto",
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

    mode = str(arguments.get("mode") or "preview").strip().lower()
    if mode not in {"status", "preview", "extract"}:
        return {
            "error": True,
            "status": "failed",
            "error_type": "invalid_tool_arguments",
            "message": "mode must be one of: status, preview, extract.",
        }

    try:
        max_chars = int(arguments.get("max_chars") or 12000)
    except (TypeError, ValueError):
        max_chars = 12000
    max_chars = max(1000, min(max_chars, 50000))

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

    payload: dict[str, Any] = {
        "status": "success",
        "tool_name": "document_reader",
        "artifact_id": str(artifact.id),
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "extraction_status": getattr(artifact, "extraction_status", None),
        "extraction_source": getattr(artifact, "extraction_source", None),
        "extraction_metadata": getattr(artifact, "extraction_metadata_json", None),
        "extraction_error": getattr(artifact, "extraction_error", None),
    }
    if mode == "status":
        return payload

    from app.services.artifact import ArtifactService

    preview = await ArtifactService(db).text_preview(artifact, max_chars=max_chars)
    payload.update(
        {
            "extraction_status": getattr(artifact, "extraction_status", None),
            "extraction_source": getattr(artifact, "extraction_source", None),
            "extraction_metadata": getattr(artifact, "extraction_metadata_json", None),
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
) -> dict[str, Any]:
    """Execute a tool call by routing to the appropriate connector."""
    if tool_name == WORKSPACE_TOOL_NAME:
        async def workspace_tool_executor(nested_tool_name: str, nested_arguments: dict[str, Any]) -> dict[str, Any]:
            return await _execute_tool_call_impl(db, user_id, nested_tool_name, nested_arguments)

        return await run_workspace(arguments, tool_executor=workspace_tool_executor)

    if tool_name == "document_reader":
        return await _execute_document_reader_tool(db, user_id, arguments)

    if tool_name == "odoo":
        arguments = _clean_odoo_arguments(arguments)
        validation_error = _validate_odoo_arguments(arguments)
        if validation_error:
            return validation_error
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
        result = await _execute_tool_call_impl(db, user_id, tool_name, arguments)
    except Exception as exc:
        if trace_svc and span_id:
            trace_svc.span_error(span_id, type(exc).__name__, str(exc))
        raise

    if trace_svc and span_id:
        has_result_error = isinstance(result, dict) and bool(result.get("error") or result.get("status") == "failed")
        handled = isinstance(result, dict) and bool(result.get("handled"))
        span_status = "success"
        if has_result_error:
            span_status = "warning" if handled else "failed"
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


def _compact_odoo_record_page(result: dict[str, Any]) -> dict[str, Any]:
    """Keep broad Odoo pages useful without letting one page dominate prompts."""
    records = result.get("records")
    if not isinstance(records, list):
        return result
    try:
        records_payload_chars = len(json.dumps(records, ensure_ascii=False, default=str))
    except Exception:
        records_payload_chars = len(str(records))
    if records_payload_chars <= MAX_ODOO_RECORD_CONTEXT_CHARS:
        return result

    visible_records = records[:MAX_ODOO_RECORD_CONTEXT_ITEMS]
    compacted = dict(result)
    compacted["records"] = visible_records
    compacted["records_compacted_for_model"] = True
    compacted["visible_record_count"] = len(visible_records)
    compacted["original_record_count"] = len(records)
    compacted["original_records_chars"] = records_payload_chars
    compacted["model_context_warning"] = (
        "Only the first records are visible in model context because the Odoo page was large. "
        "Use pagination, narrower fields, or a stricter domain for complete detail."
    )
    return compacted


def _compact_tool_result_for_model(result: Any) -> Any:
    if isinstance(result, dict) and isinstance(result.get("model"), str):
        result = _compact_odoo_record_page(result)
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


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _value_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("display_name") or value.get("id") or "")
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[1] or value[0] or "")
    return str(value or "")


def _format_quantity(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return str(value or "")
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _markdown_text(value: Any) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", text)


def _markdown_cell(value: Any) -> str:
    return _markdown_text(value).replace("|", "\\|")


def _is_structured_table_request(text: str) -> bool:
    normalized = text.lower()
    return any(phrase in normalized for phrase in (
        "table",
        "tabular",
        "spreadsheet",
        "rows",
        "list",
        "break down",
        "breakdown",
        "per product",
        "per customer",
        "per sales order",
        "per so",
        "compare",
    ))


def _structured_rows_from_odoo_result(result: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
    for payload_key in ("records", "result", "groups"):
        rows = result.get(payload_key)
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            return payload_key, rows
    return None


def _requested_fields_from_arguments(arguments: dict[str, Any]) -> list[Any] | None:
    requested = arguments.get("fields")
    if isinstance(requested, list):
        return requested
    kwargs = arguments.get("kwargs")
    if isinstance(kwargs, dict) and isinstance(kwargs.get("fields"), list):
        return kwargs["fields"]
    return None


def _structured_result_matches_request(
    *,
    result: dict[str, Any],
    arguments: dict[str, Any],
    rows: list[dict[str, Any]],
    user_text: str,
) -> bool:
    normalized = user_text.lower()
    columns = set()
    requested_fields = _requested_fields_from_arguments(arguments)
    if isinstance(requested_fields, list):
        columns.update(str(field).lower() for field in requested_fields)
    for row in rows[:10]:
        columns.update(str(key).lower() for key in row.keys())

    searchable = " ".join([
        str(result.get("model") or arguments.get("model") or "").lower(),
        " ".join(columns),
    ]).replace(".", "_")
    requirement_groups: list[tuple[str, ...]] = []
    if re.search(r"\b(?:so|sales?\s+orders?)\b", normalized):
        requirement_groups.append(("sale", "order"))
    if re.search(r"\b(?:deliver(?:ed|y)?|ordered|quantity|qty|compare)\b", normalized):
        requirement_groups.append(("qty", "quantity", "deliver", "uom", "amount"))
    if re.search(r"\binvoices?\b", normalized):
        requirement_groups.append(("invoice", "move", "amount"))

    return all(any(term in searchable for term in group) for group in requirement_groups)


def _is_complete_structured_result(result: dict[str, Any], payload_key: str) -> bool:
    if payload_key == "groups":
        return True
    return result.get("complete") is True


def _column_label(column: str) -> str:
    labels = {
        "id": "ID",
        "name": "Name",
        "display_name": "Name",
        "order_id": "Order",
        "partner_id": "Customer",
        "order_partner_id": "Customer",
        "product_id": "Product",
        "product_uom_qty": "Ordered Qty",
        "qty_delivered": "Delivered Qty",
        "qty_invoiced": "Invoiced Qty",
        "price_unit": "Unit Price",
        "amount_total": "Total",
        "amount_untaxed": "Untaxed",
        "move_id": "Journal Entry",
        "journal_id": "Journal",
        "account_id": "Account",
        "tax_ids": "Taxes",
        "tax_line_id": "Tax Line",
        "tax_tag_ids": "Tax Tags",
        "parent_state": "State",
        "record_url": "Link",
        "state": "Status",
        "__difference": "Difference",
    }
    if column in labels:
        return labels[column]
    return column.replace("_", " ").replace(".", " ").title()


def _safe_table_columns(rows: list[dict[str, Any]], arguments: dict[str, Any]) -> tuple[list[str], int]:
    excluded = {
        "record_url",
        "datas",
        "raw",
        "raw_html",
        "html",
        "content_base64",
        "base64",
        "binary",
    }
    requested = _requested_fields_from_arguments(arguments)
    columns: list[str] = []
    if isinstance(requested, list):
        for field in requested:
            field_name = str(field)
            if field_name not in excluded and any(field_name in row for row in rows):
                columns.append(field_name)
    if not columns:
        for row in rows:
            for key in row.keys():
                if key in excluded or key in columns:
                    continue
                columns.append(key)
    if "product_uom_qty" in columns and "qty_delivered" in columns:
        columns.append("__difference")
    max_columns = 10
    hidden_count = max(0, len(columns) - max_columns)
    return columns[:max_columns], hidden_count


def _table_value(row: dict[str, Any], column: str) -> Any:
    if column == "__difference":
        try:
            return _format_quantity(float(row.get("qty_delivered") or 0) - float(row.get("product_uom_qty") or 0))
        except (TypeError, ValueError):
            return ""
    value = row.get(column)
    if value is False or value is None:
        return ""
    if isinstance(value, dict):
        return _value_name(value)
    if isinstance(value, (list, tuple)):
        return _value_name(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _format_quantity(value)
    return value


def _group_column_from_request(text: str, columns: list[str]) -> str | None:
    normalized = text.lower()
    candidates: list[str] = []
    if "per product" in normalized or "by product" in normalized:
        candidates.extend(["product_id", "product", "default_code"])
    if "per customer" in normalized or "by customer" in normalized:
        candidates.extend(["order_partner_id", "partner_id", "customer_id"])
    if "per sales order" in normalized or "per so" in normalized or "by sales order" in normalized:
        candidates.extend(["order_id", "sale_order_id"])
    if "per invoice" in normalized or "by invoice" in normalized:
        candidates.extend(["move_id", "invoice_id"])
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _grouped_table_columns(columns: list[str], group_column: str) -> list[str]:
    table_columns = [column for column in columns if column != group_column]
    if group_column == "product_id":
        table_columns = [column for column in table_columns if column not in {"name", "display_name"}]
    return table_columns or columns


def _numeric_column_totals(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    totals = []
    for column in columns:
        values: list[float] = []
        for row in rows:
            if column == "__difference":
                try:
                    value = float(row.get("qty_delivered") or 0) - float(row.get("product_uom_qty") or 0)
                except (TypeError, ValueError):
                    continue
            else:
                value = row.get(column)
            if isinstance(value, bool):
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        if values and len(values) == len(rows):
            totals.append(f"{_column_label(column)}: {_format_quantity(sum(values))}")
    return totals[:4]


def _render_markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    lines = [
        "| " + " | ".join(_markdown_cell(_column_label(column)) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(_markdown_cell(_table_value(row, column)) for column in columns)
            + " |"
        )
    return lines


def _odoo_structured_table_answer(
    *,
    result: dict[str, Any],
    arguments: dict[str, Any],
    user_text: str,
) -> str | None:
    rows_payload = _structured_rows_from_odoo_result(result)
    if not rows_payload:
        return None
    payload_key, rows = rows_payload
    if not rows or len(rows) > STRUCTURED_CHAT_TABLE_ROW_LIMIT:
        return None
    if not _is_complete_structured_result(result, payload_key):
        return None
    if not _structured_result_matches_request(result=result, arguments=arguments, rows=rows, user_text=user_text):
        return None

    columns, hidden_column_count = _safe_table_columns(rows, arguments)
    if not columns:
        return None

    model_name = str(result.get("model") or arguments.get("model") or "Odoo records")
    total_rows = result.get("total_count") if isinstance(result.get("total_count"), int) else len(rows)
    lines = [
        f"Found {total_rows:,} {model_name} rows.",
        "",
        "The table below is rendered directly from the complete structured connector result.",
    ]
    if hidden_column_count:
        lines.append(f"{hidden_column_count} additional columns were left out to keep the chat table readable.")

    group_column = _group_column_from_request(user_text, columns)
    if group_column:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(_table_value(row, group_column) or "Unspecified"), []).append(row)
        table_columns = _grouped_table_columns(columns, group_column)
        for group_name in sorted(grouped.keys(), key=str.lower):
            group_rows = grouped[group_name]
            totals = _numeric_column_totals(group_rows, table_columns)
            lines.extend(["", f"### {_markdown_text(group_name)}", f"Rows: {len(group_rows):,}"])
            if totals:
                lines.append("Totals: " + " | ".join(totals))
            lines.append("")
            lines.extend(_render_markdown_table(group_rows, table_columns))
        return "\n".join(lines)

    totals = _numeric_column_totals(rows, columns)
    if totals:
        lines.extend([
            "",
            "Totals: " + " | ".join(totals),
        ])
    lines.append("")
    lines.extend(_render_markdown_table(rows, columns))
    return "\n".join(lines)


def _structured_answer_from_tool_results(
    tool_results: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> str | None:
    user_text = _latest_user_text(messages)
    if not _is_structured_table_request(user_text):
        return None

    for tool_result in reversed(tool_results):
        if tool_result.get("tool_name") != "odoo":
            continue
        result = tool_result.get("result")
        if not isinstance(result, dict):
            continue
        arguments = tool_result.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        answer = _odoo_structured_table_answer(result=result, arguments=arguments, user_text=user_text)
        if answer:
            return answer
    return None


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
        is_skipped = result_status == "skipped"
        if not has_error and not is_skipped:
            continue

        arguments = tool_result.get("arguments") if isinstance(tool_result.get("arguments"), dict) else {}
        message = str(result.get("message") or result.get("error") or result.get("error_type") or "Tool returned an error.")
        summary.append({
            "index": index,
            "tool_name": tool_result.get("tool_name"),
            "status": result_status or ("failed" if has_error else "unknown"),
            "handled": bool(result.get("handled")),
            "error_type": str(result.get("error_type") or "tool_error"),
            "message": _truncate_tool_text(message, 500),
            "arguments": _safe_tool_error_arguments(arguments),
        })
        if len(summary) >= TOOL_ERROR_SUMMARY_LIMIT:
            break
    return summary


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


def _azure_tool_summary_says_not_connected(tool_error_summary: list[dict[str, Any]]) -> bool:
    for item in tool_error_summary:
        tool_name = str(item.get("tool_name") or "")
        if MICROSOFT_TOOL_PROVIDER_BY_NAME.get(tool_name) != "azure_cli":
            continue
        error_type = str(item.get("error_type") or "").lower()
        message = str(item.get("message") or "").lower()
        if error_type == "not_connected" or "not connected" in message:
            return True
    return False


def _azure_tool_summary_has_connected_access_error(tool_error_summary: list[dict[str, Any]]) -> bool:
    for item in tool_error_summary:
        tool_name = str(item.get("tool_name") or "")
        if MICROSOFT_TOOL_PROVIDER_BY_NAME.get(tool_name) != "azure_cli":
            continue
        error_type = str(item.get("error_type") or "").lower()
        message = str(item.get("message") or "").lower()
        if error_type == "not_connected":
            continue
        haystack = f"{error_type} {message}"
        if any(marker in haystack for marker in AZURE_CONNECTED_ACCESS_ERROR_MARKERS):
            return True
    return False


def _guard_connected_system_denial(
    content: str,
    connected_systems: set[str],
    tool_error_summary: list[dict[str, Any]],
) -> str:
    if "azure_cli" not in connected_systems or not content:
        return content
    if not AZURE_FALSE_DENIAL_RE.search(content):
        return content
    if _azure_tool_summary_says_not_connected(tool_error_summary):
        return content
    if _azure_tool_summary_has_connected_access_error(tool_error_summary):
        return content

    logger.warning("Correcting assistant response that contradicted connected Azure CLI connector")
    return (
        "Azure CLI is connected for this user. I cannot verify Azure cost figures or a cost "
        "breakdown unless they come from a successful Azure Cost Management tool result. For this request, "
        "I should query Cost Management through `ms_azure_cli` using `az rest`, or report the exact command, RBAC, "
        "billing, or permission error if that query fails."
    )


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
    return _last_user_message(messages).strip()

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
        "When the user asks about data from a connected system, call the appropriate tool "
        "rather than saying you cannot access it. "
        "Use tools proactively when relevant. "
        "When the previous assistant message proposed a connected-system action plan and the user approves "
        "or clarifies it, continue by calling the relevant tool in the same response. Do not only say "
        "`starting now`, `let me proceed`, or `I will do that`; either execute with the tool or state the blocker."
    )

    odoo_available = [name for name in available_names if name == "odoo"]
    guidance_parts: list[str] = []
    if WORKSPACE_TOOL_NAME in available_names:
        guidance_parts.append(
            "\n\n### Workspace Guidance\n"
            "Workspace is the platform cloud-computer surface. Use `workspace` when the task benefits from a short "
            "Python script, shell/terminal commands, iteration over records, aggregation, data cleanup, calculations, "
            "or temporary files. "
            "For connected-system investigations that need loops or joins, prefer a compact Workspace script over "
            "many chat-level tool calls. Workspace Python can import `ai_platform_tools` and call any available "
            "platform tool/connector by name with `call(tool_name, arguments)`. Shell/terminal scripts can call "
            "`ai-platform-tool <tool_name> '<json arguments>'`. Odoo convenience helpers are still available through "
            "`ai_platform_odoo`, but they are wrappers around the raw Odoo tool; Odoo permissions come from the "
            "connected Odoo user account. "
            "When using Odoo from Workspace, prefer set-based calls: use `search_read`, `read`, `read_group`, "
            "`search_count`, and domains such as `('id', 'in', ids)` or `('res_id', 'in', ids)` instead of calling "
            "the connector once per record in a loop. Pull related records in bulk where the connector supports it "
            "and group or join results locally in the workspace. "
            "Keep scripts focused, print the final facts needed for the answer, and write small output files only when useful."
        )
    if "odoo" in odoo_available:
        guidance_parts.append(
            "\n\n### Connected Account Tool Guidance\n"
            "Use one consolidated tool per connected system. Do not invent feature-specific connector tools."
        )
        guidance_parts.append(
            "Odoo: use `odoo` for direct Odoo RPC access. Provide `model`, `method`, `args`, and `kwargs` "
            "for one raw call, or `calls` for ordered raw calls. Credentials are already supplied from the "
            "connected Odoo account. Prefer bulk domains, batch reads, and `read_group` over per-record loops."
        )
        guidance_parts.append(
            "Use Odoo tool results to inspect, act, and verify. Do not invent Odoo web URLs, domains, hostnames, "
            "fields, or record IDs."
        )
        guidance_parts.append("Odoo permissions come from the connected Odoo user account.")
    if MICROSOFT_NATIVE_TOOL_NAMES.intersection(available_names):
        guidance_parts.append(
            "Native Microsoft tools: use only these broad native-interface tools: `ms_azure_cli`, `ms_graph`, "
            "`ms_exchange_powershell`, `ms_teams_powershell`, and `ms_sharepoint_pnp_powershell`. "
            "Do not invent detailed Microsoft tools and do not call removed generic or duplicate Microsoft tools. "
            "These are separate connectors: Azure CLI uses `azure_cli`; Graph/Intune/Entra use "
            "`microsoft_graph`; Exchange uses `exchange_online`; Teams uses `teams_admin`; SharePoint/PnP uses "
            "`sharepoint_pnp`. Do not claim all Microsoft access is broken when only one native connector fails. "
            "Each connector is delegated per signed-in user and limited by that user's platform roles/RBAC plus consent "
            "for the relevant Microsoft API resource. A connected Microsoft connector does not by itself prove Azure "
            "Resource Manager, Exchange, Intune, Teams, or SharePoint access; verify the specific operation with the "
            "relevant tool result before saying it is accessible. "
            "Use `ms_azure_cli` for Azure Resource Manager CLI commands, `ms_graph` for direct Microsoft Graph requests, "
            "`ms_exchange_powershell` for Exchange Online PowerShell, "
            "`ms_teams_powershell` for Teams PowerShell, `ms_sharepoint_pnp_powershell` for SharePoint/PnP PowerShell, "
            "and `ms_azure_cli` for Azure deployment/template commands. "
            "For Azure Cost Management or spend questions, do not use `az costmanagement query`; use `ms_azure_cli` with "
            "`az rest --method post --url https://management.azure.com/subscriptions/{subscriptionId}/providers/"
            "Microsoft.CostManagement/query?api-version=2023-03-01` and a JSON body with type=Usage, "
            "timeframe=Custom, timePeriod.from/to, dataset.granularity=Daily, and "
            "dataset.aggregation.totalCost={name: PreTaxCost, function: Sum}. "
            "For 'what is costing so much' questions, query a grouped Cost Management breakdown, for example by "
            "ResourceName, ResourceGroupName, ServiceName, MeterCategory, or MeterSubCategory, then answer from the "
            "successful tool result only. Never invent Azure cost totals or breakdowns from prior assistant text, "
            "and do not turn a failed command into a disconnected-connector claim unless the tool result says not_connected. "
            "For Microsoft 365/Entra user management, use `ms_graph` with POST/PATCH/GET /users; "
            "do not say there is no Microsoft user-management tool while `ms_graph` is available. "
            "If a Microsoft user/group/license write fails, report the exact Graph permission or admin-role "
            "error and ask for the missing consent/role; do not downgrade that to 'no write-capable connector'. "
            "`ms_graph` GET collection requests auto-follow @odata.nextLink; do not invent manual $skip paging for /users. "
            "In Microsoft PowerShell tools, call Connect-AIPlatformExchange or Connect-AIPlatformTeams before using "
            "authenticated cmdlets when those tools require it. "
            "Do not use this connector for GitHub; use `github_cli` for GitHub work."
        )
    if "github_cli" in available_names:
        guidance_parts.append(
            "GitHub: use `github_cli` only. Use native gh/git/rg/jq commands; GitHub permissions decide access."
        )
    if "document_reader" in available_names:
        guidance_parts.append(
            "Documents: use `document_reader` for uploaded PDFs/images when the injected attachment preview is missing "
            "or insufficient. It is read-only; use the artifact id from the attachment context."
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


async def _odoo_currency_context(
    db: AsyncSession,
    user_id: Optional[UUID],
    snapshot: Optional[ConnectedAccountsSnapshot] = None,
) -> tuple[str, str, str | None]:
    if not user_id:
        return "", "none", None
    if snapshot is None:
        acct_result = await db.execute(
            select(AIConnectedAccount).where(
                AIConnectedAccount.user_id == user_id,
                AIConnectedAccount.provider == "odoo",
                or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
            )
        )
        account = acct_result.scalars().first()
    else:
        account = snapshot.first_connected("odoo")
    if not account or not account.odoo_currency_code:
        return "", "none", None

    code = account.odoo_currency_code
    symbol = account.odoo_currency_symbol or code
    company = account.odoo_company_name or "your company"
    currency_text = f"{company} uses {code} ({symbol})"
    return f"## Connected Odoo Currency\n{currency_text}", "odoo_connected_account", currency_text


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
        section, injected.currency_source, injected.currency_text = await _odoo_currency_context(db, user_id, snapshot)
        injected.system_prompt = _append_context_section(injected.system_prompt, section)
    except Exception as exc:
        logger.warning("Failed to inject Odoo currency context: %s", exc)

    try:
        section, injected.memories = await _memory_context(db, user_id)
        injected.system_prompt = _append_context_section(injected.system_prompt, section)
    except Exception as exc:
        logger.warning("Failed to inject memories: %s", exc)

    logger.info(
        "Context injected | memories=%d user_id=%s currency=%s",
        len(injected.memories),
        user_id,
        injected.currency_text or "none",
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
    """Consume provider streaming internally without exposing raw deltas to chat."""
    return None


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

        result = await client.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tool_definitions if tool_definitions else None,
            stream_event_sink=_discard_provider_stream_event,
        )
        result = _coerce_text_tool_calls(result, tool_definitions)
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
) -> list[dict[str, Any]]:
    tool_results: list[dict[str, Any]] = []
    for iteration in range(MAX_TOOL_LOOP_ITERATIONS):
        if state.result.get("error"):
            break
        tool_calls = state.result.get("tool_calls")
        if not tool_calls:
            break

        tool_calls = [_canonicalize_tool_call(call) for call in tool_calls]
        state.result["tool_calls"] = tool_calls
        state.stats.tool_calls += len(tool_calls)
        messages.append({
            "role": "assistant",
            "content": state.result.get("content") or None,
            "tool_calls": [
                {"id": call["id"], "type": call["type"], "function": call["function"]}
                for call in tool_calls
            ],
        })

        for call in tool_calls:
            if call.get("type") != "function":
                continue
            function = call.get("function", {})
            name = function.get("name", "")
            try:
                args = json.loads(function.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}

            result = await _execute_tool_call(db, user_id, name, args, trace_svc=trace_svc)
            if isinstance(result, dict):
                await _record_delegated_tool_auth_failure(db, user_id, name, result)
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

        structured_answer = _structured_answer_from_tool_results(tool_results, messages)
        if structured_answer:
            state.result = {
                "error": False,
                "content": structured_answer,
                "finish_reason": "structured_tool_result",
                "tool_calls": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 0,
                "model": state.result.get("model"),
            }
            break

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
        )
    else:
        if state.result.get("tool_calls"):
            state.result = {
                "error": False,
                "content": (
                    "The operation stopped because the model requested more tool calls after the allowed tool steps. "
                    "No further tools were run, so I did not guess or continue with an incomplete action."
                ),
                "finish_reason": "tool_loop_limit",
                "tool_calls": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 0,
                "model": state.result.get("model"),
            }
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
        "currency_source": injected.currency_source,
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
    )
    tool_results: list[dict[str, Any]] = []
    tool_results.extend(await _run_tool_loop(
        db, user_id, state, full_messages, tools, tool_definitions, temperature, max_tokens,
        trace_svc=trace_svc,
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
    content = _guard_connected_system_denial(
        str(state.result.get("content") or ""),
        connected_accounts.connected_systems,
        tool_error_summary,
    )

    response = {
        "content": content,
        "finish_reason": state.result.get("finish_reason", ""),
        "model_provider": state.used_provider.name,
        "model_name": state.used_model.display_name,
        "prompt_tokens": state.stats.prompt_tokens,
        "completion_tokens": state.stats.completion_tokens,
        "total_tokens": state.stats.total_tokens,
        "latency_ms": state.stats.latency_ms,
        "tool_calls": tool_results if tool_results else None,
        "tool_error_summary": tool_error_summary if tool_error_summary else None,
        "has_tool_errors": bool(tool_error_summary),
        "context": _context_metadata(injected, state, policy),
        "tool_call_count": state.stats.tool_calls,
    }
    return response
