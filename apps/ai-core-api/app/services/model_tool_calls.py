import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from app.models.models import AITool
from app.services.tool_registry import CONSOLIDATED_TOOL_NAMES, MICROSOFT_NATIVE_TOOL_NAMES

logger = logging.getLogger(__name__)

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

MICROSOFT_TOOL_NAME_ALIASES = {
    "microsoft_graph": "ms_graph",
    "microsoft_graph_api": "ms_graph",
    "graph_api": "ms_graph",
}
GRAPH_API_VERSION_SEGMENTS = {"v1.0", "beta"}


def _normalize_tool_name(name: str) -> str:
    """Replace invalid chars with underscores, cap at 64 chars."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


def _strip_function_prefix(name: str) -> str:
    value = (name or "").strip()
    if value.lower().startswith("functions."):
        value = value[len("functions."):]
    if ":" in value:
        value = value.split(":", 1)[0]
    return value.strip()


def _normalize_ms_graph_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments or {})
    raw_target = normalized.get("path") or normalized.pop("url", None) or normalized.pop("endpoint", None)
    if raw_target is None:
        return normalized

    target = str(raw_target).strip()
    if not target:
        return normalized

    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme.lower() not in {"http", "https"} or parsed.netloc.lower() != "graph.microsoft.com":
            normalized["path"] = target
            return normalized

    path = parsed.path or target
    query = parsed.query
    parts = [part for part in path.split("/") if part]
    if parts and parts[0].lower() in GRAPH_API_VERSION_SEGMENTS:
        if not normalized.get("api_version"):
            normalized["api_version"] = parts[0]
        path = "/" + "/".join(parts[1:])
    if not path.startswith("/"):
        path = f"/{path}"
    if query:
        path = f"{path}?{query}"
    normalized["path"] = path
    return normalized


def _canonical_tool_invocation(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    cleaned = _strip_function_prefix(name)
    normalized = _normalize_tool_name(cleaned)
    normalized_lower = normalized.lower()
    canonical_names = {
        "ms_azure_cli",
        "ms_graph",
        "ms_exchange_powershell",
        "ms_teams_powershell",
        "ms_sharepoint_pnp_powershell",
        "github_cli",
        "odoo_ops_runner",
        "document_reader",
    }
    mapped = MICROSOFT_TOOL_NAME_ALIASES.get(
        normalized_lower,
        normalized_lower if normalized_lower in canonical_names else cleaned,
    )
    if mapped == "ms_graph":
        return mapped, _normalize_ms_graph_arguments(arguments)
    if mapped in MICROSOFT_NATIVE_TOOL_NAMES or mapped in {"github_cli", "odoo_ops_runner", "document_reader"}:
        return mapped, arguments
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


def _build_tool_definitions(tools: list[AITool]) -> list[dict[str, Any]]:
    """Convert AITool records to OpenAI-compatible tool definitions."""
    definitions = []
    for tool in tools:
        schema = tool.input_schema
        if not schema:
            continue
        normalized = _normalize_tool_name(tool.name)
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
