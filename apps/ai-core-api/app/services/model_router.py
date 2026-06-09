import asyncio
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
    AIProvider, AIModel, AIRoute, AIUsageLog, AIConnectedAccount, AITool, AICompanyFact,
    AIMemory, AIArtifact,
)
from app.services.foundry_client import FoundryClient
from app.services.context import ContextService
from app.services.key_vault import get_secret_value, key_vault_uri
from app.services.connected_account_state import effective_connected_accounts, upsert_delegated_account
from app.schemas.schemas import ContextRequest
from app.services.tool_registry import CONSOLIDATED_TOOL_NAMES

logger = logging.getLogger(__name__)

ROUTE_NOT_CONFIGURED_MESSAGE = "AI chat is not configured yet. Please ask an administrator to configure a model in Settings \u2192 AI Configuration."
RATE_LIMIT_ERROR_TYPES = {"rate_limit_exceeded", "quota_exceeded"}
MAX_TOOL_RESULT_STRING_CHARS = 600
MAX_TOOL_STDIO_STRING_CHARS = 8000
MAX_TOOL_RESULT_LIST_ITEMS = 5
MAX_TOOL_RESULT_RECORD_ITEMS = 80
MAX_TOOL_RESULT_DICT_KEYS = 60
MAX_TOOL_RESULT_JSON_CHARS = 50000
MAX_ODOO_RECORD_CONTEXT_CHARS = 20000
MAX_ODOO_RECORD_CONTEXT_ITEMS = 25
TOOL_LOOP_RESPONSE_MAX_TOKENS = 4000
TOOL_FINALIZER_MAX_TOKENS = 4000
TOOL_FINALIZER_MAX_TOOL_CALLS = 12
TOOL_FINALIZER_RESULT_CHARS = 20000
TOOL_FINALIZER_PAYLOAD_CHARS = 70000
TOOL_FINALIZER_CHAT_MESSAGES = 8
TOOL_FINALIZER_CHAT_MESSAGE_CHARS = 2000
TOOL_ERROR_SUMMARY_LIMIT = 8
DIRECT_BLANK_RETRY_MAX_TOKENS = 1000
DIRECT_BLANK_RETRY_MESSAGE = {
    "role": "system",
    "content": (
        "Your previous response returned no user-visible content. "
        "Answer the latest user message now, concisely, without calling tools. "
        "If the available conversation does not contain enough evidence, say exactly what is missing."
    ),
}
CHAT_TITLE_MAX_CHARS = 70
CHAT_TITLE_SOURCE_MESSAGES = 6
CHAT_TITLE_SOURCE_CHARS = 900
CHAT_TITLE_MAX_TOKENS = 24
TOOL_LOOP_FOLLOWUP_MESSAGE = {
    "role": "system",
    "content": (
        "Use the tool results already gathered to answer the user. "
        "Call another tool only when a necessary fact is still missing. "
        "Keep the final answer concise, and state any uncertainty instead of reasoning at length."
    ),
}
CHAT_TITLE_SYSTEM_PROMPT = (
    "Generate a short title for this chat. "
    "Use 3 to 6 words. "
    "Return only the title, with no quotes, no markdown, no trailing punctuation, and no generic words like chat or conversation."
)
CHAT_TITLE_FILLER_WORDS = {
    "a", "all", "an", "and", "are", "as", "at", "be", "been", "being", "can", "could",
    "did", "do", "does", "for", "from", "get", "give", "go", "how", "i", "if",
    "in", "is", "it", "list", "me", "my", "of", "on", "or", "our", "please",
    "show", "so", "tell", "the", "there", "this", "to", "today", "tomorrow", "us", "was",
    "we", "were", "what", "when", "where", "why", "with", "would", "you", "your",
    "yesterday",
}
CHAT_TITLE_CANONICAL_WORDS = {
    "ai": "AI",
    "api": "API",
    "azure": "Azure",
    "github": "GitHub",
    "odoo": "Odoo",
    "mcp": "MCP",
}
TEXT_TOOL_CALL_RE = re.compile(
    r"<\|?tool_call_begin\|?>\s*(?P<name>.*?)\s*"
    r"<\|?tool_call_arguments?_begin\|?>\s*(?P<arguments>.*?)\s*"
    r"<\|?tool_call_end\|?>",
    re.DOTALL | re.IGNORECASE,
)
TEXT_TOOL_CALL_COMPACT_RE = re.compile(
    r"<\|?tool_call_begin\|?>\s*(?P<name>[^\s<>{]+)\s*(?P<body>.*?)\s*<\|?tool_call_end\|?>",
    re.DOTALL | re.IGNORECASE,
)
TEXT_TOOL_CALL_BLOCK_RE = re.compile(
    r"<\|?tool_call(?:_begin)?\|?>\s*(?P<body>.*?)\s*(?:<\|?tool_call_end\|?>|</tool_call>)",
    re.DOTALL | re.IGNORECASE,
)
TEXT_TOOL_CALL_SECTION_RE = re.compile(
    r"<\|?tool_calls_section_begin\|?>.*?<\|?tool_calls_section_end\|?>",
    re.DOTALL | re.IGNORECASE,
)
TEXT_TOOL_MARKER_RE = re.compile(r"<\|?tool_call", re.IGNORECASE)
TEXT_TOOL_ARGUMENT_MARKER_RE = re.compile(r"<\|?tool_call_arguments?_begin\|?>", re.IGNORECASE)
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
    "tasks, connected accounts, and business systems. "
    "You are not tied to one system. "
    "You may use connected tools such as Odoo, GitHub, Microsoft Admin, and documents "
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
    "Keep responses practical, business-focused, and clear."
)

DEFAULT_PLATFORM_TIMEZONE = os.environ.get("PLATFORM_TIMEZONE", "Africa/Johannesburg")
FOLLOW_UP_CONTEXT_WORDS = {
    "same", "again", "this", "that", "these", "those", "it", "she", "he",
    "they", "them", "her", "him", "full", "more", "all", "timeline",
}
FOLLOW_UP_GREETINGS = {"hi", "hello", "hey", "thanks", "thank you"}
FOLLOW_UP_CORRECTION_WORDS = {
    "actually", "correction", "meant", "mean", "instead", "rather", "sorry",
    "previous", "earlier",
}
MONTH_WORDS = {
    "jan", "january", "feb", "february", "mar", "march", "apr", "april",
    "may", "jun", "june", "jul", "july", "aug", "august", "sep", "sept",
    "september", "oct", "october", "nov", "november", "dec", "december",
}
DATE_LIKE_RE = re.compile(r"\b\d{1,4}([/-]\d{1,2}){1,2}\b|\b\d{1,2}(st|nd|rd|th)?\b", re.IGNORECASE)


@dataclass
class InjectedContext:
    system_prompt: str
    rules: list[Any] = field(default_factory=list)
    facts: list[Any] = field(default_factory=list)
    memories: list[Any] = field(default_factory=list)
    search_results: list[dict[str, Any]] = field(default_factory=list)
    subtasks: list[dict[str, Any]] = field(default_factory=list)
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
    client: FoundryClient
    stats: ModelCallStats
    fallback_used: bool = False
    fallback_model_display: str = "none"
    fallback_reason: str = "noneeded"


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


async def _resolve_api_key(provider: AIProvider) -> Optional[str]:
    """Try Key Vault secret first, then env var, then fall back to hard-coded."""
    if provider.auth_type == "key_vault_secret" and provider.secret_reference:
        try:
            secret_value = await get_secret_value(provider.secret_reference)
            if secret_value:
                return secret_value
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


KNOWN_CONNECTOR_TYPES = ["odoo", "github", "azure"]

CONNECTOR_DISPLAY_NAMES: dict[str, str] = {
    "odoo": "Odoo",
    "github": "GitHub",
    "azure": "Microsoft Admin",
    "slack": "Slack",
    "teams": "Microsoft Teams",
}

ODOO_CONNECTOR_URL: str = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY: str = os.environ.get("ODOO_CONNECTOR_API_KEY", "")
DELEGATED_AUTH_FAILURE_MARKERS = (
    "does not exist in msal token cache",
    "run `az login`",
    "azure cli profile",
    "azure is not connected",
    "azure token is expired",
    "microsoft admin is not connected",
    "microsoft delegated credentials",
)


def _normalize_tool_name(name: str) -> str:
    """Replace invalid chars with underscores, cap at 64 chars."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


TOOL_NAME_MAP: dict[str, str] = {}
ODOO_OPS_RUNNER_MODES = {
    "health",
    "schema",
    "query",
    "records",
    "count",
    "aggregate",
    "report",
    "account_report",
    "attachment",
    "content",
    "message",
    "mutation",
    "write",
    "create",
    "delete",
    "execute",
}
ODOO_RECORDSET_METHODS_REQUIRE_IDS = {
    "message_post",
    "message_subscribe",
    "message_unsubscribe",
    "action_feedback",
    "action_done",
    "action_cancel",
    "unlink",
    "write",
}
ODOO_RECORDSET_METHOD_PREFIXES = ("action_", "button_", "message_")
ODOO_SIDE_EFFECT_METHODS_REQUIRE_VERIFICATION = {"message_post", "action_feedback", "action_done"}
ODOO_QUERY_SHAPE_KEYS = {"domain", "fields", "limit", "offset", "order"}


def _strip_function_prefix(name: str) -> str:
    value = (name or "").strip()
    if value.lower().startswith("functions."):
        value = value[len("functions."):]
    if ":" in value:
        value = value.split(":", 1)[0]
    return value.strip()


def _odoo_positional_arg(arguments: dict[str, Any], index: int, default: Any = None) -> Any:
    args = arguments.get("args")
    if isinstance(args, list) and len(args) > index:
        return args[index]
    return default


def _odoo_kwargs(arguments: dict[str, Any]) -> dict[str, Any]:
    kwargs = arguments.get("kwargs")
    return kwargs if isinstance(kwargs, dict) else {}


def _looks_like_odoo_query(arguments: dict[str, Any]) -> bool:
    if not str(arguments.get("model") or "").strip():
        return False
    if arguments.get("mode") or arguments.get("operation") or arguments.get("method"):
        return False
    if arguments.get("values") is not None:
        return False
    if isinstance(arguments.get("ids"), list):
        return False
    return any(key in arguments for key in ODOO_QUERY_SHAPE_KEYS)


def _odoo_query_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    kwargs = _odoo_kwargs(arguments)
    domain = arguments.get("domain", _odoo_positional_arg(arguments, 0, []))
    fields = arguments.get("fields", _odoo_positional_arg(arguments, 1, kwargs.get("fields")))
    converted: dict[str, Any] = {
        "mode": "query",
        "model": arguments.get("model"),
        "domain": domain if isinstance(domain, list) else [],
    }
    if isinstance(fields, list):
        converted["fields"] = fields
    for key in ("limit", "offset", "order"):
        value = arguments.get(key, kwargs.get(key))
        if value is not None:
            converted[key] = value
    return converted


def _odoo_read_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    kwargs = _odoo_kwargs(arguments)
    ids = arguments.get("ids", _odoo_positional_arg(arguments, 0, []))
    fields = arguments.get("fields", _odoo_positional_arg(arguments, 1, kwargs.get("fields")))
    converted: dict[str, Any] = {
        "mode": "records",
        "model": arguments.get("model"),
        "ids": ids if isinstance(ids, list) else [],
    }
    if isinstance(fields, list):
        converted["fields"] = fields
    return converted


def _odoo_mutation_arguments(arguments: dict[str, Any], operation: str) -> dict[str, Any]:
    converted: dict[str, Any] = {
        "mode": operation,
        "operation": operation,
        "model": arguments.get("model"),
    }
    if operation == "create":
        converted["values"] = arguments.get("values", _odoo_positional_arg(arguments, 0, {}))
    elif operation == "write":
        converted["ids"] = arguments.get("ids", _odoo_positional_arg(arguments, 0, []))
        converted["values"] = arguments.get("values", _odoo_positional_arg(arguments, 1, {}))
    elif operation == "delete":
        converted["ids"] = arguments.get("ids", _odoo_positional_arg(arguments, 0, []))
    return converted


def _odoo_alias_to_ops_runner(alias: str, arguments: dict[str, Any]) -> dict[str, Any]:
    method = str(arguments.get("method") or "").strip()
    normalized_alias = alias.lower()
    operation = normalized_alias.removeprefix("odoo_")
    if normalized_alias == "odoo":
        operation = method
    if operation in {"search_read", "search"}:
        return _odoo_query_arguments(arguments)
    if operation in {"read", "browse"}:
        return _odoo_read_arguments(arguments)
    if operation == "search_count":
        return {
            "mode": "count",
            "model": arguments.get("model"),
            "domain": arguments.get("domain", _odoo_positional_arg(arguments, 0, [])),
        }
    if operation in {"fields_get", "schema"}:
        return {
            "mode": "schema",
            "model": arguments.get("model"),
            "fields": arguments.get("fields"),
        }
    if operation in {"create", "write", "unlink", "delete"}:
        return _odoo_mutation_arguments(arguments, "delete" if operation == "unlink" else operation)
    if method:
        converted = {
            "mode": "execute",
            "model": arguments.get("model"),
            "method": method,
            "args": arguments.get("args") or [],
            "kwargs": arguments.get("kwargs") or {},
        }
        for key in ("ids", "record_id"):
            if key in arguments:
                converted[key] = arguments[key]
        return converted
    if _looks_like_odoo_query(arguments):
        return _odoo_query_arguments(arguments)
    return {"mode": "execute", **arguments}


def _canonical_tool_invocation(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    cleaned = _strip_function_prefix(name)
    normalized = _normalize_tool_name(cleaned)
    normalized_lower = normalized.lower()
    mapped = TOOL_NAME_MAP.get(normalized) or TOOL_NAME_MAP.get(normalized_lower)
    if not mapped:
        mapped = (
            normalized_lower
            if (
                normalized_lower in {"ms_admin", "azure_cli", "github_cli", "odoo_ops_runner"}
                or normalized_lower == "odoo"
                or normalized_lower.startswith("odoo_")
            )
            else cleaned
        )
    if mapped == "azure_cli":
        converted = dict(arguments)
        converted.setdefault("mode", "azure_cli")
        return "ms_admin", converted
    if mapped in {"ms_admin", "github_cli", "odoo_ops_runner"}:
        return mapped, arguments
    if mapped == "odoo" or mapped.startswith("odoo_"):
        return "odoo_ops_runner", _odoo_alias_to_ops_runner(mapped, arguments)
    return mapped, arguments


def _parse_tool_arguments(arguments_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments_text or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start: idx + 1]
    return ""


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < existing_end and end > existing_start for existing_start, existing_end in spans)


def _tool_arguments_text(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, str):
        return _extract_first_json_object(value) or value.strip()
    return "{}"


def _json_envelope_tool_invocation(body: str) -> tuple[str, str] | None:
    payload = _parse_tool_arguments(_extract_first_json_object(body))
    if not payload:
        return None

    function = payload.get("function") if isinstance(payload.get("function"), dict) else {}
    raw_name = (
        payload.get("name")
        or payload.get("tool_name")
        or payload.get("recipient_name")
        or function.get("name")
    )
    if not raw_name:
        return None

    argument_value = None
    argument_found = False
    for source in (payload, function):
        if not isinstance(source, dict):
            continue
        for key in ("arguments", "parameters", "params", "input", "args"):
            if key in source:
                argument_value = source[key]
                argument_found = True
                break
        if argument_found:
            break

    if not argument_found:
        argument_value = {
            key: value
            for key, value in payload.items()
            if key not in {"id", "type", "name", "tool_name", "recipient_name", "function"}
        }

    return str(raw_name), _tool_arguments_text(argument_value)


def _iter_text_tool_invocations(content: str) -> list[tuple[str, str, tuple[int, int]]]:
    invocations: list[tuple[str, str, tuple[int, int]]] = []
    matched_spans: list[tuple[int, int]] = []

    for match in TEXT_TOOL_CALL_RE.finditer(content):
        span = match.span()
        matched_spans.append(span)
        invocations.append((match.group("name"), match.group("arguments").strip(), span))

    for match in TEXT_TOOL_CALL_COMPACT_RE.finditer(content):
        span = match.span()
        if _overlaps(span, matched_spans):
            continue
        body = TEXT_TOOL_ARGUMENT_MARKER_RE.sub(" ", match.group("body") or "")
        arguments = _extract_first_json_object(body)
        if not arguments:
            continue
        matched_spans.append(span)
        invocations.append((match.group("name"), arguments, span))

    for match in TEXT_TOOL_CALL_BLOCK_RE.finditer(content):
        span = match.span()
        if _overlaps(span, matched_spans):
            continue
        body = TEXT_TOOL_ARGUMENT_MARKER_RE.sub(" ", match.group("body") or "").strip()
        json_invocation = _json_envelope_tool_invocation(body)
        if json_invocation:
            matched_spans.append(span)
            invocations.append((json_invocation[0], json_invocation[1], span))
            continue

        arguments = _extract_first_json_object(body)
        if not arguments:
            continue
        raw_name = body[: body.find(arguments)].strip()
        if not raw_name:
            continue
        matched_spans.append(span)
        invocations.append((raw_name, arguments, span))

    return invocations


def _strip_text_tool_call_markup(content: str, spans: list[tuple[int, int]]) -> str:
    stripped = TEXT_TOOL_CALL_SECTION_RE.sub("", content)
    if stripped != content:
        return stripped.strip()

    for start, end in sorted(spans, reverse=True):
        stripped = stripped[:start] + stripped[end:]
    return stripped.strip()


def _textual_tool_call_allowed(canonical_name: str, tool_definitions: list[dict[str, Any]]) -> bool:
    if canonical_name in CONSOLIDATED_TOOL_NAMES:
        return True

    exposed_tool_names = {
        str(((definition.get("function") or {}).get("name")) or "")
        for definition in tool_definitions
        if isinstance(definition, dict)
    }
    return canonical_name in exposed_tool_names


def _coerce_text_tool_calls(result: dict[str, Any], tool_definitions: list[dict[str, Any]]) -> dict[str, Any]:
    if result.get("error") or result.get("tool_calls"):
        return result
    content = str(result.get("content") or "")
    if not TEXT_TOOL_MARKER_RE.search(content):
        return result

    calls: list[dict[str, Any]] = []
    matched_spans: list[tuple[int, int]] = []
    for idx, (raw_name, raw_arguments, span) in enumerate(_iter_text_tool_invocations(content), start=1):
        parsed_arguments = _parse_tool_arguments(raw_arguments)
        canonical_name, canonical_arguments = _canonical_tool_invocation(raw_name, parsed_arguments)
        if not _textual_tool_call_allowed(canonical_name, tool_definitions):
            continue
        matched_spans.append(span)
        calls.append({
            "id": f"text_call_{idx}",
            "type": "function",
            "function": {
                "name": canonical_name,
                "arguments": json.dumps(canonical_arguments, ensure_ascii=False, default=str),
            },
        })

    if not calls:
        return result

    cleaned_content = _strip_text_tool_call_markup(content, matched_spans)
    result["content"] = cleaned_content or None
    result["tool_calls"] = calls
    result["finish_reason"] = "tool_calls"
    result["text_tool_calls_detected"] = True
    return result


def _canonicalize_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    if call.get("type") != "function":
        return call
    function = dict(call.get("function") or {})
    arguments = _parse_tool_arguments(str(function.get("arguments") or "{}"))
    canonical_name, canonical_arguments = _canonical_tool_invocation(str(function.get("name") or ""), arguments)
    normalized = dict(call)
    normalized["function"] = {
        **function,
        "name": canonical_name,
        "arguments": json.dumps(canonical_arguments, ensure_ascii=False, default=str),
    }
    return normalized


def _connector_error_payload(raw_detail: Any, fallback_text: str = "") -> dict[str, Any]:
    detail = raw_detail.get("detail") if isinstance(raw_detail, dict) and "detail" in raw_detail else raw_detail
    if not isinstance(detail, dict):
        message = str(detail or fallback_text or "Connector returned an error.")
        return {
            "error_type": "connector_http_error",
            "message": _truncate_tool_text(message, 1200),
        }

    error_type = str(detail.get("error_type") or detail.get("error") or "connector_error")
    raw_message = detail.get("message") or detail.get("detail") or fallback_text or error_type
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


def _odoo_execute_method_requires_record_ids(method: str | None) -> bool:
    normalized = (method or "").strip()
    return normalized in ODOO_RECORDSET_METHODS_REQUIRE_IDS or normalized.startswith(ODOO_RECORDSET_METHOD_PREFIXES)


def _odoo_execute_has_record_ids(arguments: dict[str, Any]) -> bool:
    ids = arguments.get("ids")
    if isinstance(ids, list) and any(isinstance(item, int) and not isinstance(item, bool) for item in ids):
        return True

    record_id = arguments.get("record_id")
    if isinstance(record_id, int) and not isinstance(record_id, bool):
        return True

    args = arguments.get("args")
    if not isinstance(args, list) or not args:
        return False
    first_arg = args[0]
    if isinstance(first_arg, int) and not isinstance(first_arg, bool):
        return True
    return isinstance(first_arg, list) and any(isinstance(item, int) and not isinstance(item, bool) for item in first_arg)


def _normalize_odoo_ops_runner_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    mode = str(normalized.get("mode") or "").strip()
    if not mode and _looks_like_odoo_query(normalized):
        normalized["mode"] = "query"
        mode = "query"
    if mode == "message" and not normalized.get("operation"):
        normalized["operation"] = "post"
    return normalized


def _handled_odoo_schema_connector_error(
    arguments: dict[str, Any],
    detail: dict[str, Any],
    status_code: int,
) -> dict[str, Any] | None:
    mode = str(arguments.get("mode") or "").strip()
    if mode != "schema":
        return None

    raw_error_type = str(detail.get("error_type") or "connector_error")
    handled_schema_errors = {
        "model_unavailable",
        "schema_unavailable",
        "invalid_domain_field",
        "odoo_error",
        "connector_http_error",
        "internal_error",
    }
    if raw_error_type not in handled_schema_errors or status_code not in {400, 404, 422, 500}:
        return None

    model = str(arguments.get("model") or detail.get("model") or "unknown")
    error_type = raw_error_type if raw_error_type in {"model_unavailable", "schema_unavailable"} else "schema_unavailable"
    message = (
        detail.get("message")
        if raw_error_type in {"model_unavailable", "schema_unavailable"}
        else f"Odoo model '{model}' could not be inspected by this connected account, so the schema probe was skipped."
    )
    return {
        "error": True,
        "handled": True,
        "status": "skipped",
        "error_type": error_type,
        "message": _truncate_tool_text(str(message), 600),
        "model": model,
        "status_code": status_code,
        "connector_error": detail,
        "suggestion": "Use mode 'schema' with query to discover installed models, or inspect a different candidate model.",
    }


def _validate_odoo_ops_runner_arguments(arguments: dict[str, Any]) -> dict[str, Any] | None:
    mode = str(arguments.get("mode") or "").strip()
    if not mode:
        return _handled_tool_argument_error(
            "The Odoo tool call was missing the required mode, so it was skipped before reaching the connector.",
            missing=["mode"],
            suggestion="Retry with mode set to schema, query, records, count, report, attachment, content, message, mutation, or execute.",
        )
    if mode not in ODOO_OPS_RUNNER_MODES:
        return _handled_tool_argument_error(
            f"Unknown Odoo tool mode: {mode}.",
            suggestion="Use one of the supported Odoo ops runner modes.",
        )
    if mode == "attachment" and not arguments.get("attachment_id") and not arguments.get("attachment_ids"):
        return _handled_tool_argument_error(
            "The Odoo attachment request was missing attachment_id or attachment_ids, so it was skipped before reaching the connector.",
            missing=["attachment_id", "attachment_ids"],
            suggestion="Query ir.attachment first, then call attachment mode with attachment_id or attachment_ids.",
        )
    if mode == "execute":
        method = str(arguments.get("method") or "").strip()
        if _odoo_execute_method_requires_record_ids(method) and not _odoo_execute_has_record_ids(arguments):
            return _handled_tool_argument_error(
                f"The Odoo method '{method}' is record-bound and cannot be called without target record IDs.",
                missing=["ids", "record_id", "args[0]"],
                suggestion=(
                    "Retry with ids, record_id, or args=[[id]]. For chatter posts, prefer mode 'message' "
                    "with model, record_id, operation='post', and body. For mail.activity completion, "
                    "use mode 'execute' with ids=[activity_id]."
                ),
            )
    return None


def _odoo_side_effect_requires_verification(arguments: dict[str, Any]) -> bool:
    mode = str(arguments.get("mode") or "").strip()
    if mode == "message":
        return True
    if mode != "execute":
        return False
    method = str(arguments.get("method") or "").strip()
    return method in ODOO_SIDE_EFFECT_METHODS_REQUIRE_VERIFICATION


def _guard_unverified_odoo_side_effect(arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if not _odoo_side_effect_requires_verification(arguments):
        return result
    if result.get("error") or result.get("effect_verified") is True:
        return result

    guarded = dict(result)
    guarded.update(
        {
            "error": True,
            "handled": True,
            "status": "unverified_side_effect",
            "error_type": "unverified_side_effect",
            "message": (
                "The Odoo side-effect call returned, but the connector did not verify that the change "
                "persisted. Do not claim the activity was completed or the message was sent."
            ),
        }
    )
    guarded.setdefault("verification", {"status": "missing"})
    return guarded


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
    if account.secret_reference and key_vault_uri():
        try:
            api_key = await get_secret_value(account.secret_reference)
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
    if tool_name == "document_reader":
        return await _execute_document_reader_tool(db, user_id, arguments)

    if tool_name.startswith("odoo_"):
        if tool_name == "odoo_ops_runner":
            arguments = _normalize_odoo_ops_runner_arguments(arguments)
            validation_error = _validate_odoo_ops_runner_arguments(arguments)
            if validation_error:
                return validation_error
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
                raw_detail = response.json()
            except Exception:
                raw_detail = {"error_type": "connector_http_error", "message": response.text}
            detail = _connector_error_payload(raw_detail, response.text)
            handled = _handled_odoo_schema_connector_error(arguments, detail, response.status_code)
            if handled:
                return handled
            return {
                "error": True,
                "status_code": response.status_code,
                "connector_error": detail,
                "error_type": detail.get("error_type") or "connector_error",
                "message": detail.get("message") or "Connector returned an error.",
            }
        result = response.json()
        if tool_name == "odoo_ops_runner" and isinstance(result, dict):
            return _guard_unverified_odoo_side_effect(arguments, result)
        return result

    if tool_name in ("ms_admin", "github_cli"):
        from app.services.connector_commands import run_github_cli_command, run_ms_admin_tool

        command = str(arguments.get("command", ""))
        timeout = int(arguments.get("timeout", 60))
        if tool_name == "ms_admin":
            return await run_ms_admin_tool(arguments, user_id, timeout=timeout)
        return await run_github_cli_command(command, user_id, timeout=timeout)

    return {"error": f"Unknown tool: {tool_name}"}


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
    if not user_id or tool_name != "ms_admin" or result.get("status") != "failed":
        return

    message = " ".join(
        str(result.get(key) or "")
        for key in ("error", "message", "stderr")
    ).strip()
    lower_message = message.lower()
    if not any(marker in lower_message for marker in DELEGATED_AUTH_FAILURE_MARKERS):
        return

    status = "expired" if "expired" in lower_message else "error"
    await upsert_delegated_account(
        db,
        "azure",
        user_id,
        status=status,
        permission_summary=message[:500] if message else "Microsoft delegated credentials are not usable.",
    )


def _truncate_tool_text(value: str, limit: int = MAX_TOOL_RESULT_STRING_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... [truncated {len(value) - limit} chars]"


def _sanitize_chat_title(title: Any) -> str | None:
    text = str(title or "").strip()
    if not text or "<|tool_call" in text or "```" in text:
        return None

    text = re.split(r"[\r\n]+", text, maxsplit=1)[0]
    text = re.sub(r"^\s*(?:[-*#]+|\d+[.)])\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \t'\"`“”‘’")
    text = text.rstrip(".:;,- ")
    if not text:
        return None

    if text.lower() in {"new chat", "untitled", "chat", "conversation"}:
        return None

    if len(text) > CHAT_TITLE_MAX_CHARS:
        text = text[:CHAT_TITLE_MAX_CHARS].rsplit(" ", 1)[0].strip() or text[:CHAT_TITLE_MAX_CHARS].strip()
    return text or None


def _title_word(token: str) -> str:
    canonical = CHAT_TITLE_CANONICAL_WORDS.get(token.lower())
    if canonical:
        return canonical
    if token.isupper() and len(token) <= 6:
        return token
    return token[:1].upper() + token[1:].lower()


def _fallback_chat_title(messages: list[dict[str, Any]]) -> str | None:
    """Create a concise local title when the title model is unavailable."""
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = str(message.get("content") or "").strip()
        if not text:
            continue

        text = TEXT_TOOL_CALL_SECTION_RE.sub(" ", text)
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"[_*`~#>\[\]{}()]", " ", text)
        tokens = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", text)
        if not tokens:
            continue

        useful = [token for token in tokens if token.lower() not in CHAT_TITLE_FILLER_WORDS and not token.isdigit()]
        selected = useful[:6] or tokens[:6]
        title = _sanitize_chat_title(" ".join(_title_word(token) for token in selected))
        if title:
            return title
    return None


def _chat_title_source_text(messages: list[dict[str, Any]]) -> str:
    excerpts: list[str] = []
    for message in messages[:CHAT_TITLE_SOURCE_MESSAGES]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        content = re.sub(r"\s+", " ", content)
        excerpts.append(f"{role}: {_truncate_tool_text(content, CHAT_TITLE_SOURCE_CHARS)}")
    return "\n".join(excerpts)


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
        item_limit = MAX_TOOL_RESULT_RECORD_ITEMS if key_lower in {"records", "result", "lines", "groups"} else MAX_TOOL_RESULT_LIST_ITEMS
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


def _is_blank_model_content(result: dict[str, Any]) -> bool:
    return not str(result.get("content") or "").strip()


def _recent_chat_messages_for_finalizer(messages: list) -> list[dict[str, str]]:
    recent: list[dict[str, str]] = []
    for message in messages[-TOOL_FINALIZER_CHAT_MESSAGES:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = message.get("content")
        if content is None:
            continue
        text = str(content).strip()
        if not text:
            continue
        recent.append({
            "role": role,
            "content": _truncate_tool_text(text, TOOL_FINALIZER_CHAT_MESSAGE_CHARS),
        })
    return recent


def _json_or_preview(value: Any, limit: int) -> Any:
    payload = json.dumps(value, ensure_ascii=False, default=str)
    if len(payload) <= limit:
        return value
    return {
        "truncated": True,
        "original_chars": len(payload),
        "preview": payload[:limit],
        "warning": "Only a preview is available. Do not infer missing values from truncated output.",
    }


def _tool_results_payload_for_finalizer(tool_results: list[dict[str, Any]]) -> str:
    visible_results = tool_results[-TOOL_FINALIZER_MAX_TOOL_CALLS:]
    first_index = max(len(tool_results) - len(visible_results) + 1, 1)
    payload: dict[str, Any] = {
        "tool_call_count": len(tool_results),
        "included_tool_calls": len(visible_results),
        "tool_results": [],
    }
    dropped = len(tool_results) - len(visible_results)
    if dropped:
        payload["dropped_earlier_tool_calls"] = dropped
        payload["warning"] = "Earlier tool calls were omitted from this finalization payload."

    for offset, tool_result in enumerate(visible_results):
        payload["tool_results"].append({
            "call_index": first_index + offset,
            "tool_name": tool_result.get("tool_name"),
            "arguments": _json_or_preview(tool_result.get("arguments") or {}, 2000),
            "result": _json_or_preview(tool_result.get("result"), TOOL_FINALIZER_RESULT_CHARS),
        })

    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= TOOL_FINALIZER_PAYLOAD_CHARS:
        return text
    return (
        text[:TOOL_FINALIZER_PAYLOAD_CHARS]
        + f"\n...[finalizer payload truncated from {len(text)} chars; answer only from visible evidence]..."
    )


def _build_tool_finalizer_messages(messages: list, tool_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    conversation = json.dumps(_recent_chat_messages_for_finalizer(messages), ensure_ascii=False, default=str)
    tool_payload = _tool_results_payload_for_finalizer(tool_results)
    return [
        {
            "role": "system",
            "content": (
                "You are finalizing a user-visible answer after connected-system tools have already run. "
                "Do not call tools. Use only the conversation excerpt and tool results provided. "
                "If the evidence is enough, answer directly and concisely. "
                "If the evidence is partial, truncated, or blocked by tool errors, say exactly what is known "
                "and what is still missing. Do not invent data."
            ),
        },
        {
            "role": "user",
            "content": (
                "Conversation excerpt:\n"
                f"{conversation}\n\n"
                "Tool results:\n"
                f"{tool_payload}\n\n"
                "Write the final answer for the user now."
            ),
        },
    ]


def _report_error_message(tool_result: dict[str, Any], result: dict[str, Any]) -> str:
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
        report_name = tool_result.get("arguments", {}).get("report_name", "unknown")
        return (
            f"I could not find a report named \"{report_name}\" in Odoo. "
            "This may be because the report module is not installed or the name is different. "
            "Try using the report discovery tool to list available reports."
        )
    if "Technical error" in raw_message:
        return (
            "I reached Odoo, but could not execute the report. "
            f"The report engine encountered an internal issue: {raw_message}. "
            "This usually means the report could not be resolved or executed "
            "with the current Odoo account. Please check Accounting report access, "
            "Odoo edition/version, or use the report discovery diagnostic "
            "to confirm the available report names."
        )
    return (
        "I reached Odoo, but could not execute the report. "
        f"Reason: {raw_message}. "
        "This may be due to report permissions, Odoo edition/version differences, "
        "or unsupported report options."
    )


def _report_line_items(lines: list[dict[str, Any]], currency_symbol: str) -> list[str]:
    items = []
    for line in lines[:10]:
        name = line.get("name", "")
        value = line.get("formatted_value") or ""
        if name and value:
            items.append(f"{name}: {currency_symbol}{value}")
        elif name:
            items.append(name)
    return items


def _report_success_message(result: dict[str, Any]) -> str | None:
    lines = result.get("lines") or []
    report_name = result.get("report_name") or "report"
    currency_symbol = result.get("currency_symbol") or ""
    date_from = result.get("date_from") or ""
    date_to = result.get("date_to") or ""
    available = result.get("available_line_names") or []
    missing = result.get("missing_line_names") or []

    if not lines:
        if not available:
            return None
        period = f" for {date_from} to {date_to}" if date_from and date_to else ""
        return (
            f"I opened the {report_name} report{period}, but could not find matching lines. "
            f"Available top-level lines include: {', '.join(available[:10])}."
        )

    parts = [f"From the Odoo {report_name}"]
    if date_from and date_to:
        parts.append(f"for {date_from} to {date_to}")
    parts.append(":")

    items = _report_line_items(lines, currency_symbol)
    if items:
        parts.append("")
        parts.extend(f"  - {item}" for item in items)
    if len(lines) > 10:
        parts.append(f"  ... and {len(lines) - 10} more lines")
    if missing:
        parts.append(f"Note: requested lines not found in report: {', '.join(missing[:5])}")
    return "\n".join(parts)


def _build_report_fallback_answer(tool_results: list[dict]) -> str | None:
    """Build a clean user-facing answer when report tool output needs summarising."""
    for tool_result in tool_results:
        if tool_result.get("tool_name") != "odoo_ops_runner":
            continue
        arguments = tool_result.get("arguments") or {}
        if tool_result.get("tool_name") == "odoo_ops_runner" and arguments.get("mode") not in ("report", "account_report"):
            continue
        result = tool_result.get("result", {})
        if not isinstance(result, dict):
            continue
        if result.get("error"):
            return _report_error_message(tool_result, result)
        message = _report_success_message(result)
        if message:
            return message
    return None


def _strip_html_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _odoo_records_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    records = result.get("records")
    if records is None:
        records = result.get("result")
    if isinstance(records, dict) and isinstance(records.get("items"), list):
        records = records["items"]
    if isinstance(records, list):
        return [record for record in records if isinstance(record, dict)]
    return []


def _odoo_record_timestamp(record: dict[str, Any]) -> str:
    for key in ("date", "create_date", "write_date"):
        value = record.get(key)
        if value:
            return str(value)
    return ""


def _odoo_display_value(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("name"):
            return str(value["name"])
        if value.get("id") is not None:
            return str(value["id"])
        return ", ".join(f"{k}={v}" for k, v in list(value.items())[:3])
    if isinstance(value, list):
        return ", ".join(_odoo_display_value(item) for item in value[:3])
    if value is False or value is None:
        return ""
    return str(value)


def _odoo_record_summary(model: str, record: dict[str, Any]) -> str:
    name = (
        record.get("display_name")
        or record.get("record_name")
        or record.get("name")
        or record.get("summary")
        or record.get("subject")
    )
    if not name and record.get("body"):
        name = _strip_html_text(record.get("body"))
    if not name:
        name = f"{model} #{record.get('id') or record.get('res_id') or '?'}"

    extras: list[str] = []
    for key in ("model", "res_model", "move_type", "state", "message_type", "payment_type"):
        value = _odoo_display_value(record.get(key))
        if value:
            extras.append(value)
    partner = _odoo_display_value(record.get("partner_id"))
    if partner:
        extras.append(partner)
    amount = record.get("amount_total", record.get("amount"))
    if amount not in (None, ""):
        extras.append(f"amount={amount}")
    origin = _odoo_display_value(record.get("invoice_origin"))
    if origin:
        extras.append(f"origin={origin}")

    summary = _strip_html_text(name)
    if extras:
        summary = f"{summary} ({'; '.join(extras[:4])})"
    return _truncate_tool_text(summary, 260)


def _build_odoo_evidence_fallback_answer(tool_results: list[dict[str, Any]]) -> str | None:
    """Build a deterministic answer when the model cannot finalize Odoo tool output."""
    odoo_results = [item for item in tool_results if item.get("tool_name") == "odoo_ops_runner"]
    if not odoo_results:
        return None

    timeline: list[tuple[str, str, str]] = []
    counts: list[str] = []
    errors: list[str] = []
    omitted = 0

    for tool_result in odoo_results:
        arguments = tool_result.get("arguments") or {}
        result = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
        model = str(result.get("model") or arguments.get("model") or "Odoo")
        if result.get("error"):
            error_type = str(result.get("error_type") or "tool_error")
            message = str(result.get("message") or result.get("error_type") or "tool error")
            errors.append(f"{model}: {error_type}: {_truncate_tool_text(message, 220)}")
            continue

        returned = result.get("returned_count", result.get("count"))
        total = result.get("total_count")
        if isinstance(returned, int):
            if isinstance(total, int) and total != returned:
                counts.append(f"{model}: {returned} of {total} records returned")
            else:
                counts.append(f"{model}: {returned} records")

        records = _odoo_records_from_result(result)
        for record in records:
            timestamp = _odoo_record_timestamp(record)
            if not timestamp:
                omitted += 1
                continue
            timeline.append((timestamp, model, _odoo_record_summary(model, record)))

    if timeline:
        timeline.sort(key=lambda item: item[0])
        max_items = 40
        visible = timeline[:max_items]
        lines = ["Here is the Odoo timeline I could reconstruct from the connector results:"]
        for timestamp, model, summary in visible:
            lines.append(f"- {timestamp} - {model}: {summary}")
        hidden = len(timeline) - len(visible)
        if hidden > 0:
            lines.append(f"- ... {hidden} more timestamped Odoo records were omitted from this fallback summary.")
        if omitted:
            lines.append(f"Note: {omitted} Odoo records had no timestamp field and were not placed on the timeline.")
        if errors:
            lines.append("Connector notes: " + "; ".join(errors[:3]))
        return "\n".join(lines)

    if counts or errors:
        lines = ["I gathered Odoo connector evidence, but there were no timestamped records to turn into a timeline."]
        if counts:
            lines.append("Record counts: " + "; ".join(counts[:6]))
        if errors:
            lines.append("Connector notes: " + "; ".join(errors[:3]))
        return "\n".join(lines)

    return None


def _build_tool_fallback_answer(tool_results: list[dict[str, Any]]) -> str | None:
    return _build_report_fallback_answer(tool_results) or _build_odoo_evidence_fallback_answer(tool_results)


def _safe_tool_error_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "command", "query", "model", "mode", "operation", "method", "resource",
        "timeout", "report_name", "fields", "limit", "order",
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


def _map_odoo_tool_to_path(tool_name: str) -> str:
    return "/odoo/ops/run" if tool_name == "odoo_ops_runner" else ""


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
    if not latest:
        return ""
    lower = latest.lower()
    if lower in FOLLOW_UP_GREETINGS:
        return latest

    tokens = set(re.findall(r"[a-z0-9_&+-]+", lower))
    has_date_or_month = bool(tokens.intersection(MONTH_WORDS)) or bool(DATE_LIKE_RE.search(lower))
    is_follow_up = (
        len(tokens) <= 3
        or (len(tokens) <= 8 and bool(tokens.intersection(FOLLOW_UP_CORRECTION_WORDS)))
        or (len(tokens) <= 8 and has_date_or_month)
        or bool(tokens.intersection(FOLLOW_UP_CONTEXT_WORDS))
        or lower in {"?", "??"}
    )
    if not is_follow_up:
        return latest

    recent: list[str] = []
    for message in messages[-6:]:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "").strip()
        if content:
            recent.append(content[:1000])
    return "\n".join(recent) if recent else latest


def _risk_level_for_message(user_msg_text: str) -> str:
    q = user_msg_text.lower()
    is_finance_topic = any(kw in q for kw in [
        "revenue", "income", "expense", "profit", "loss", "balance", "invoice",
        "bill", "payment", "cost", "price", "tax", "vat", "accounting",
    ])

    is_odoo_lookup = any(phrase in q for phrase in [
        "check odoo", "odoo", "account.move", "ir.attachment", "credit note",
        "find", "search", "look up",
    ])
    if is_odoo_lookup and q.count("amount") <= 2:
        contains_aggregate_intent = any(kw in q for kw in [
            "compare", "reconcile", "audit", "forecast", "budget", "analyze",
            "trend", "variance", "total revenue", "total income", "net profit",
        ])
        if not contains_aggregate_intent:
            is_finance_topic = False

    return "high" if is_finance_topic else "low"


async def _select_route_model_provider(
    db: AsyncSession,
    task_type: str,
    risk_level: str,
) -> tuple[AIRoute, AIModel, AIProvider, dict[str, Any]]:
    from app.services.model_routing_policy import ModelRoutingPolicyService

    policy = await ModelRoutingPolicyService(db).select_route(
        task_type=task_type,
        risk_level=risk_level,
        requires_tools=task_type == "general_chat",
    )
    route_id = policy.get("selected_route_id")
    model_id = policy.get("selected_model_id")

    if route_id and model_id:
        route_res = await db.execute(select(AIRoute).where(AIRoute.id == UUID(route_id)))
        route = route_res.scalar_one_or_none()
        model_res = await db.execute(select(AIModel).where(AIModel.id == UUID(model_id)))
        model = model_res.scalar_one_or_none()
        if route and model:
            prov_res = await db.execute(select(AIProvider).where(AIProvider.id == model.provider_id))
            provider = prov_res.scalar_one_or_none()
            if provider:
                return route, model, provider, policy

    route, model, provider = await get_enabled_route(db, task_type)
    return route, model, provider, policy


def _append_tool_guidance(system_prompt: str, tools: list[AITool], tool_definitions: list[dict[str, Any]]) -> str:
    if not tool_definitions:
        return system_prompt

    available_names = [tool.name for tool in tools]
    system_prompt += (
        "\n\nYou have access to the following tools. "
        "When the user asks about data from a connected system, call the appropriate tool "
        "rather than saying you cannot access it. "
        "Use tools proactively when relevant."
    )

    odoo_available = [name for name in available_names if name.startswith("odoo_")]
    guidance_parts: list[str] = []
    if "odoo_ops_runner" in odoo_available:
        guidance_parts.append(
            "\n\n### Connected Account Tool Guidance\n"
            "Use one consolidated tool per connected system. Do not invent feature-specific connector tools."
        )
        guidance_parts.append("Odoo: use `odoo_ops_runner` only. Select a broad mode for the operation.")
        guidance_parts.append(
            "Modes: health, schema, query, aggregate, report, attachment, content, message, mutation, execute. "
            "For create/write/delete, use mode `mutation` with the `operation` field."
        )
        guidance_parts.append(
            "Odoo query/content results include returned_count, total_count, has_more, and complete. "
            "If complete is true, do not describe the result as truncated. "
            "If the user asks for all records, full detail, or a timeline and has_more is true, "
            "fetch the next page with the same query and a higher offset before answering."
        )
        guidance_parts.append(
            "Do not invent Odoo web URLs, domains, or hostnames. Only provide Odoo links from connector results "
            "such as record_url or record_urls. If no verified connector-provided URL is available, say you "
            "cannot provide a verified link instead of guessing."
        )
        guidance_parts.append(
            "Use Odoo `schema` to inspect models and fields. Use `query` with explicit fields for record lists. "
            "Use `content` only for text/body fields on a narrow domain or specific ids; never use broad unfiltered "
            "`content` calls for schema discovery or general user/activity lookups."
        )
        guidance_parts.append(
            "For chatter/activity lookups: `mail.activity` uses `res_model` and `res_id`; "
            "`mail.message` uses `model` and `res_id` for the related business record. "
            "Do not filter `mail.message` by `res_model`."
        )
        guidance_parts.append(
            "Use Odoo mode `message` to post chatter comments with model, record_id, operation='post', and body. "
            "Odoo message_post posts to the target record's chatter; it is not a private Discuss direct message. "
            "Do not tell the user a private/direct message was sent unless a direct-message-capable tool result "
            "explicitly verifies that delivery. "
            "For record methods in mode `execute` such as message_post, action_feedback, action_done, "
            "button_validate, or unlink, always include ids/record_id or args=[[id]]. Never call these with empty args."
        )
        guidance_parts.append(
            "For Odoo side effects such as message_post and mail.activity action_feedback/action_done, only say the "
            "message was sent or the activity was marked done when the tool result has effect_verified=true. "
            "If the result is unverified, say the call could not be verified and do not present it as completed."
        )
        guidance_parts.append(
            "Report aliases: P&L/PNL -> Profit and Loss, BS/Balance Sheet, TB/Trial Balance, GL/General Ledger.\n"
            "Dates: this month -> first day to today; this year -> Jan 1 to today; last month -> previous month.\n"
            "Do not infer a report from a business metric. Use a report only when the user names the report or chooses one after discovery."
        )
        guidance_parts.append("Odoo permissions come from the connected Odoo user account.")
    if "ms_admin" in available_names:
        guidance_parts.append(
            "Microsoft Admin: use `ms_admin` only. Modes: status, azure_cli, powershell, bicep, graph_request. "
            "Use azure_cli mode for Azure CLI, powershell mode for Microsoft Graph/Exchange/Teams/Az PowerShell cmdlets, "
            "bicep mode for Bicep CLI validation/build work, and graph_request for direct Microsoft Graph calls. "
            "In powershell mode, call Connect-AIPlatformAz, Connect-AIPlatformGraph, Connect-AIPlatformExchange, "
            "or Connect-AIPlatformTeams before using authenticated Microsoft admin cmdlets. "
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


async def _connected_systems_for_context(db: AsyncSession, user_id: Optional[UUID]) -> set[str]:
    if not user_id:
        return set()
    acct_result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == user_id,
            or_(AIConnectedAccount.status == "connected", AIConnectedAccount.status == "active"),
        )
    )
    return {acct.provider for acct in acct_result.scalars().all()}


async def _business_context(
    db: AsyncSession,
    user_id: Optional[UUID],
    connected_systems: Optional[set[str]] = None,
) -> tuple[str, list[Any], list[Any]]:
    connected_systems = connected_systems if connected_systems is not None else await _connected_systems_for_context(db, user_id)
    context = await ContextService(db).get_context(
        ContextRequest(
            task="general_chat",
            systems=list(connected_systems) if connected_systems else None,
            limit=50,
        ),
        user_id=user_id,
        connected_systems=connected_systems,
    )
    rules = context.get("rules", [])
    facts = context.get("facts", [])
    prompt_parts: list[str] = []
    if rules:
        rules_text = "\n".join(f"- [Priority {rule.priority}] {rule.body}" for rule in rules)
        prompt_parts.append(
            "## Active Business Rules\n"
            "Rules are listed in precedence order. Lower priority numbers override higher priority numbers.\n"
            f"{rules_text}"
        )
    if facts:
        facts_text = "\n".join(f"- {fact.key}: {fact.value}" for fact in facts)
        prompt_parts.append(f"## Company Facts\n{facts_text}")
    return ("\n\n".join(prompt_parts), rules, facts)


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


async def _search_context(messages: list, user_id: Optional[UUID]) -> tuple[str, list[dict[str, Any]]]:
    if not messages:
        return "", []
    from app.services.search_service import SearchService
    from app.core.config import get_settings

    search_svc = SearchService()
    if not search_svc.enabled:
        return "", []

    hits = await search_svc.search_memories(
        query=_last_user_message(messages),
        user_id=user_id,
        status="active",
    )
    chunks = hits[:get_settings().azure_search_max_injected_chunks]
    if not chunks:
        return "", []

    blocks = []
    for hit in chunks:
        source_type = hit.get("type") or hit.get("source_type") or "reference"
        title = hit.get("title") or "Untitled Document"
        chunk_text = hit.get("chunk_text") or hit.get("summary") or ""
        block = f"- [{source_type}] {title}"
        if chunk_text:
            block += f"\n  Details: {chunk_text[:350]}"
        blocks.append(block)
    return "## Relevant Reference Materials\n" + "\n".join(blocks), chunks


async def _subtask_context(messages: list, db: AsyncSession) -> tuple[str, list[dict[str, Any]]]:
    user_query = _last_user_message(messages)
    if not user_query:
        return "", []
    is_reconciliation = any(kw in user_query.lower() for kw in ["compare", "reconcile", "reconciliation", "credit note", "pdf"])
    if not is_reconciliation:
        return "", []

    from app.services.task_graph import TaskGraphExecutor

    subtasks = await TaskGraphExecutor().execute_all(user_query, db=db)
    summary = [f"- Subtask '{task['name']}' ({task['status']}): Result={task['result']}" for task in subtasks]
    return "## Ephemeral Sub-Agent / Task Worker Results\n" + "\n".join(summary), subtasks


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
    connected_systems = snapshot.connected_systems if snapshot else None

    try:
        section, injected.rules, injected.facts = await _business_context(db, user_id, connected_systems)
        injected.system_prompt = _append_context_section(injected.system_prompt, section)
    except Exception as exc:
        logger.warning("Failed to inject business context: %s", exc)

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

    try:
        section, injected.search_results = await _search_context(messages, user_id)
        injected.system_prompt = _append_context_section(injected.system_prompt, section)
    except Exception as exc:
        logger.warning("Failed to retrieve or inject search results: %s", exc)

    try:
        section, injected.subtasks = await _subtask_context(messages, db)
        injected.system_prompt = _append_context_section(injected.system_prompt, section)
    except Exception as exc:
        logger.warning("Failed to execute Task Graph nodes: %s", exc)

    logger.info(
        "Context injected | rules=%d facts=%d memories=%d search_results=%d subtasks=%d user_id=%s currency=%s",
        len(injected.rules),
        len(injected.facts),
        len(injected.memories),
        len(injected.search_results),
        len(injected.subtasks),
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


async def _call_model(
    model: AIModel,
    provider: AIProvider,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    tool_definitions: list[dict[str, Any]],
    trace_svc: Any = None,
    attempt_reason: str = "primary",
    client: Optional[FoundryClient] = None,
) -> tuple[dict[str, Any], FoundryClient]:
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
            client = await build_foundry_client(provider, model)
        result = await client.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tool_definitions if tool_definitions else None,
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


def _is_rate_limit_error(result: dict[str, Any]) -> bool:
    return bool(result.get("error") and result.get("error_type") in RATE_LIMIT_ERROR_TYPES)


async def _fallback_candidates(
    db: AsyncSession,
    route: AIRoute,
    primary_model: AIModel,
    needs_tools: bool,
) -> list[tuple[AIModel, AIProvider]]:
    candidates: list[tuple[AIModel, AIProvider]] = []
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
                candidates.append((fb_model, fb_prov))

    first_candidate_supports_tools = bool(candidates) and (
        candidates[0][0].supports_tools == "true" or (candidates[0][0].config_json or {}).get("supports_tools") is True
    )
    if not needs_tools or first_candidate_supports_tools:
        return candidates

    all_models_res = await db.execute(
        select(AIModel).where(
            AIModel.enabled == "true",
            AIModel.id != primary_model.id,
            AIModel.id != (route.fallback_model_id or UUID(int=0)),
        ).limit(10)
    )
    for alt_model in all_models_res.scalars().all():
        supports_tools = alt_model.supports_tools == "true" or (alt_model.config_json or {}).get("supports_tools") is True
        if not supports_tools:
            continue
        alt_prov_res = await db.execute(
            select(AIProvider).where(AIProvider.id == alt_model.provider_id, AIProvider.enabled == "true")
        )
        alt_prov = alt_prov_res.scalar_one_or_none()
        if alt_prov:
            candidates.append((alt_model, alt_prov))
    return candidates


async def _try_rate_limit_fallbacks(
    db: AsyncSession,
    route: AIRoute,
    primary_model: AIModel,
    state: ModelCallState,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    tool_definitions: list[dict[str, Any]],
    reason: str,
    trace_svc: Any = None,
) -> ModelCallState:
    if not _is_rate_limit_error(state.result):
        return state

    state.fallback_reason = reason
    attempted_fallback = False
    for fb_model, fb_provider in await _fallback_candidates(db, route, primary_model, bool(tool_definitions)):
        if fb_model.id == state.used_model.id:
            continue
        supports_tools = fb_model.supports_tools == "true" or (fb_model.config_json or {}).get("supports_tools") is True
        if tool_definitions and not supports_tools:
            logger.warning("Fallback candidate %s does not support required tools. Skipping.", fb_model.display_name)
            continue

        attempted_fallback = True
        state.fallback_model_display = fb_model.display_name
        logger.warning(
            "Model quota exceeded, trying fallback | failed_model=%s fallback=%s reason=%s",
            state.used_model.display_name,
            fb_model.display_name,
            reason,
        )
        fb_result, fb_client = await _call_model(
            fb_model,
            fb_provider,
            messages,
            temperature,
            max_tokens,
            tool_definitions,
            trace_svc=trace_svc,
            attempt_reason=reason,
        )
        state.stats.add_result(fb_result)
        state.result = fb_result
        state.used_model = fb_model
        state.used_provider = fb_provider
        state.client = fb_client
        state.fallback_used = True
        if not fb_result.get("error"):
            return state
        state.fallback_reason = f"tried_fallback_{fb_model.display_name}_also_failed"

    if not attempted_fallback:
        state.fallback_model_display = "none"
    return state


async def _run_model_with_fallbacks(
    db: AsyncSession,
    route: AIRoute,
    primary_model: AIModel,
    primary_provider: AIProvider,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    tool_definitions: list[dict[str, Any]],
    trace_svc: Any = None,
) -> ModelCallState:
    stats = ModelCallStats()
    result, client = await _call_model(
        primary_model,
        primary_provider,
        messages,
        temperature,
        max_tokens,
        tool_definitions,
        trace_svc=trace_svc,
        attempt_reason="primary",
    )
    stats.add_result(result)
    state = ModelCallState(result=result, used_model=primary_model, used_provider=primary_provider, client=client, stats=stats)

    return await _try_rate_limit_fallbacks(
        db, route, primary_model, state, messages, temperature, max_tokens, tool_definitions,
        reason="primary_quota_exceeded",
        trace_svc=trace_svc,
    )


async def _run_tool_loop(
    db: AsyncSession,
    user_id: Optional[UUID],
    state: ModelCallState,
    route: AIRoute,
    primary_model: AIModel,
    messages: list[dict[str, Any]],
    tools: list[AITool],
    tool_definitions: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    trace_svc: Any = None,
) -> list[dict[str, Any]]:
    tool_results: list[dict[str, Any]] = []
    for _ in range(10):
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
        state = await _try_rate_limit_fallbacks(
            db, route, primary_model, state, followup_messages, temperature, followup_max_tokens, tool_definitions,
            reason="tool_loop_quota_exceeded",
            trace_svc=trace_svc,
        )
    return tool_results


def _should_finalize_blank_tool_response(state: ModelCallState, tool_results: list[dict[str, Any]]) -> bool:
    if not tool_results:
        return False
    if state.result.get("error") or state.result.get("tool_calls"):
        return False
    return _is_blank_model_content(state.result)


async def _finalize_blank_tool_response(
    db: AsyncSession,
    user_id: Optional[UUID],
    route: AIRoute,
    primary_model: AIModel,
    state: ModelCallState,
    original_messages: list,
    tool_results: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    trace_svc: Any = None,
) -> ModelCallState:
    if not _should_finalize_blank_tool_response(state, tool_results):
        return state

    fallback = _build_tool_fallback_answer(tool_results)
    if fallback:
        logger.info("Used deterministic tool fallback before finalizer | user_id=%s tool_calls=%d", user_id, len(tool_results))
        state.result["content"] = fallback
        state.result["finish_reason"] = state.result.get("finish_reason") or "fallback"
        return state

    finalizer_messages = _build_tool_finalizer_messages(original_messages, tool_results)
    finalizer_max_tokens = max(max_tokens, TOOL_FINALIZER_MAX_TOKENS)
    logger.warning(
        "Retrying blank post-tool model response with finalizer | user_id=%s finish_reason=%s tool_calls=%d max_tokens=%d",
        user_id,
        state.result.get("finish_reason", ""),
        len(tool_results),
        finalizer_max_tokens,
    )

    result, client = await _call_model(
        state.used_model,
        state.used_provider,
        finalizer_messages,
        min(temperature, 0.2),
        finalizer_max_tokens,
        [],
        trace_svc=trace_svc,
        attempt_reason="tool_finalizer",
        client=state.client,
    )
    state.result = result
    state.client = client
    state.stats.add_result(state.result)
    state = await _try_rate_limit_fallbacks(
        db,
        route,
        primary_model,
        state,
        finalizer_messages,
        min(temperature, 0.2),
        finalizer_max_tokens,
        [],
        reason="tool_finalizer_quota_exceeded",
        trace_svc=trace_svc,
    )

    if _is_blank_model_content(state.result):
        logger.warning(
            "Tool finalizer returned blank content | finish_reason=%s tool_calls=%d",
            state.result.get("finish_reason", ""),
            len(tool_results),
        )
    else:
        logger.info("Recovered blank post-tool model response with finalizer | tool_calls=%d", len(tool_results))
    return state


def _should_retry_blank_direct_response(state: ModelCallState, tool_results: list[dict[str, Any]]) -> bool:
    if tool_results:
        return False
    if state.result.get("error") or state.result.get("tool_calls"):
        return False
    return _is_blank_model_content(state.result)


async def _retry_blank_direct_response(
    db: AsyncSession,
    route: AIRoute,
    primary_model: AIModel,
    state: ModelCallState,
    messages: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    temperature: float,
    trace_svc: Any = None,
) -> ModelCallState:
    if not _should_retry_blank_direct_response(state, tool_results):
        return state

    retry_messages = messages + [DIRECT_BLANK_RETRY_MESSAGE]
    logger.warning(
        "Retrying blank direct model response without tools | finish_reason=%s max_tokens=%d",
        state.result.get("finish_reason", ""),
        DIRECT_BLANK_RETRY_MAX_TOKENS,
    )
    result, client = await _call_model(
        state.used_model,
        state.used_provider,
        retry_messages,
        min(temperature, 0.2),
        DIRECT_BLANK_RETRY_MAX_TOKENS,
        [],
        trace_svc=trace_svc,
        attempt_reason="blank_direct_retry",
        client=state.client,
    )
    state.result = result
    state.client = client
    state.stats.add_result(state.result)
    state = await _try_rate_limit_fallbacks(
        db,
        route,
        primary_model,
        state,
        retry_messages,
        min(temperature, 0.2),
        DIRECT_BLANK_RETRY_MAX_TOKENS,
        [],
        reason="blank_direct_retry_quota_exceeded",
        trace_svc=trace_svc,
    )
    if _is_blank_model_content(state.result):
        logger.warning("Blank direct model retry returned blank content | finish_reason=%s", state.result.get("finish_reason", ""))
    else:
        logger.info("Recovered blank direct model response with no-tool retry")
    return state


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
    state: ModelCallState,
    primary_model_display: str,
) -> str:
    error_type = result.get("error_type", "unknown")
    if error_type in ("rate_limit_exceeded", "quota_exceeded"):
        if state.fallback_used:
            return (
                "The AI service is temporarily unavailable because all models "
                "reached their quota or rate limit. "
                f"Tried: {primary_model_display} (primary) and {state.fallback_model_display} (fallback). "
                "Please try again shortly, or contact support if this continues."
            )
        fallback_note = f" (fallback model: {state.fallback_model_display})" if state.fallback_model_display != "none" else ""
        return (
            "The AI service is temporarily unavailable because the model "
            "quota or rate limit has been reached. "
            f"Primary: {primary_model_display}{fallback_note}. "
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
        "error_type=%s status_code=%s raw_message=%s fallback_used=%s fallback_model=%s "
        "user_id=%s chat_session_id=%s tools_enabled=%s",
        primary_model.display_name,
        primary_provider.name,
        state.used_model.display_name,
        state.used_provider.name,
        error_type,
        status_code,
        raw_message,
        state.fallback_used,
        state.fallback_model_display,
        user_id,
        chat_session_id,
        tools_enabled,
    )
    raise ProviderCallError(
        _provider_error_message(state.result, state, primary_model.display_name),
        state.used_provider.name,
        state.used_model.display_name,
    )


def _context_metadata(injected: InjectedContext, state: ModelCallState, policy: dict[str, Any], primary_model: AIModel) -> dict[str, Any]:
    return {
        "rules_injected": [{"id": str(rule.id), "title": rule.title, "priority": rule.priority} for rule in injected.rules],
        "facts_injected": [{"key": fact.key, "value": fact.value} for fact in injected.facts],
        "memories_injected": [{"id": str(memory.id), "title": memory.title, "type": memory.type} for memory in injected.memories],
        "search_results_injected": [
            {
                "id": hit.get("id"),
                "title": hit.get("title"),
                "type": hit.get("type"),
                "score": hit.get("score"),
            }
            for hit in injected.search_results
        ],
        "currency_source": injected.currency_source,
        "subtasks": injected.subtasks,
        "current_date": _platform_now().date().isoformat(),
        "model_routing": {
            "primary_model": primary_model.display_name,
            "fallback_model": state.fallback_model_display,
            "fallback_used": state.fallback_used,
            "fallback_reason": state.fallback_reason,
            "routing_reason": policy.get("reason", "unknown"),
            "cost_tier": policy.get("cost_tier", "medium"),
        },
    }


def _build_blank_direct_fallback_answer(messages: list, response: dict[str, Any]) -> str | None:
    finish_reason = str(response.get("finish_reason") or "")
    if finish_reason != "length":
        return None

    latest = _last_user_message(messages).lower()
    recent_text = " ".join(
        str(message.get("content") or "")
        for message in messages[-4:]
        if isinstance(message, dict) and message.get("role") in {"user", "assistant"}
    ).lower()
    if "odoo" in recent_text and any(term in latest for term in ("strategy", "invalid field", "activity", "timeline", "schema")):
        return (
            "Safe repeatable Odoo read-only strategy:\n"
            "- Start with `schema` for candidate models and fields; do not assume fields exist.\n"
            "- Resolve the user with `res.users`, then query activity-bearing models using fields confirmed by schema.\n"
            "- Prefer `create_uid`, `write_uid`, `create_date`, and `write_date` when the model supports them.\n"
            "- For login/device style evidence, verify the available fields on `res.users.log`, `bus.presence`, or device-related models before filtering.\n"
            "- Keep `query` fields explicit and narrow; use `content` only for specific ids or a tight domain.\n"
            "- If a model has no user-link field, report that limitation instead of inventing a relationship."
        )

    return (
        "I could not generate a usable answer for that turn because the model hit its output limit before "
        "returning visible content. No new connector result was available for a deterministic answer. "
        "Ask for a narrower slice or a shorter summary."
    )


async def _apply_blank_content_fallback(
    db: AsyncSession,
    user_id: Optional[UUID],
    messages: list,
    response: dict[str, Any],
    tool_results: list[dict[str, Any]],
    trace_svc: Any = None,
) -> dict[str, Any]:
    if not _is_blank_model_content(response):
        return response

    if tool_results:
        fallback = _build_tool_fallback_answer(tool_results)
        if fallback:
            logger.info("Used tool fallback answer (from tool results) | user_id=%s tool_calls=%d", user_id, len(tool_results))
            response["content"] = fallback
        return response

    finish_reason = str(response.get("finish_reason") or "")
    fallback = _build_blank_direct_fallback_answer(messages, response)
    if fallback:
        response["content"] = fallback
        response["finish_reason"] = "fallback"
        logger.info("Used blank direct-response fallback | user_id=%s finish_reason=%s", user_id, finish_reason)

    return response


async def generate_chat_title(
    db: AsyncSession,
    messages: list[dict[str, Any]],
    chat_session_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    request_id: Optional[str] = None,
    trace_svc: Any = None,
) -> str | None:
    """Generate a compact local chat title without making a model call.

    Title creation is a cosmetic side effect of a chat turn. It must never delay
    user-visible answers or appear as a long-running model step in the activity
    stream.
    """
    _ = (db, chat_session_id, user_id, request_id, trace_svc)
    return _fallback_chat_title(messages)


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
        risk_level = _risk_level_for_message(user_msg_text)
        route, model_obj, provider, policy = await _select_route_model_provider(db, task_type, risk_level)
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
                    "rules_injected": len(injected.rules),
                    "facts_injected": len(injected.facts),
                    "memories_injected": len(injected.memories),
                    "search_results_injected": len(injected.search_results),
                    "subtasks_injected": len(injected.subtasks),
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

    state = await _run_model_with_fallbacks(
        db, route, model_obj, provider, full_messages, temperature, max_tokens, tool_definitions,
        trace_svc=trace_svc,
    )
    tool_results: list[dict[str, Any]] = []
    tool_results.extend(await _run_tool_loop(
        db, user_id, state, route, model_obj, full_messages, tools, tool_definitions, temperature, max_tokens,
        trace_svc=trace_svc,
    ))
    state = await _finalize_blank_tool_response(
        db,
        user_id,
        route,
        model_obj,
        state,
        messages,
        tool_results,
        temperature,
        max_tokens,
        trace_svc=trace_svc,
    )
    state = await _retry_blank_direct_response(
        db,
        route,
        model_obj,
        state,
        full_messages,
        tool_results,
        temperature,
        trace_svc=trace_svc,
    )
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
        "content": state.result.get("content", ""),
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
        "context": _context_metadata(injected, state, policy, model_obj),
        "tool_call_count": state.stats.tool_calls,
    }
    return await _apply_blank_content_fallback(db, user_id, messages, response, tool_results, trace_svc=trace_svc)
