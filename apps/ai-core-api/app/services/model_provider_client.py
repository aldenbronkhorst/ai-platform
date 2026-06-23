import time
import re
import json
from typing import Optional, Any, Callable

import httpx


QUOTA_RATE_LIMIT_PATTERNS = [
    re.compile(r"rate\s*limit", re.IGNORECASE),
    re.compile(r"quota", re.IGNORECASE),
    re.compile(r"requests?\s*limit", re.IGNORECASE),
    re.compile(r"too\s*many\s*requests", re.IGNORECASE),
    re.compile(r"capacity", re.IGNORECASE),
    re.compile(r"throttl", re.IGNORECASE),
    re.compile(r"429", re.IGNORECASE),
]


def _classify_error(status_code: int, message: str) -> str:
    if status_code == 429:
        return "rate_limit_exceeded"
    if status_code == 401:
        return "authentication_error"
    if status_code == 404:
        return "model_not_found"
    if status_code == 403:
        for pat in QUOTA_RATE_LIMIT_PATTERNS:
            if pat.search(message):
                return "quota_exceeded"
        return "authorization_error"
    if status_code >= 500:
        return "server_error"
    for pat in QUOTA_RATE_LIMIT_PATTERNS:
        if pat.search(message):
            return "rate_limit_exceeded"
    if status_code == 400:
        return "bad_request"
    return "unknown"


def _error_detail(body: Any, default_detail: str) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or default_detail)
        if isinstance(error, str):
            return error
        message = body.get("message")
        if message:
            return str(message)
    return default_detail


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    return f"{normalized}/chat/completions"


def _default_extra_body(base_url: str) -> dict[str, Any]:
    return {}


def _text_delta(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        return text if isinstance(text, str) else ""
    return ""


def _reasoning_delta(delta: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning", "thinking", "thought"):
        text = _text_delta(delta.get(key))
        if text:
            return text
    return ""


def _is_word_duplicate(previous: str, incoming: str) -> int:
    previous_match = re.search(r"([\w'-]{3,})\s*$", previous)
    incoming_match = re.match(r"\s*([\w'-]{3,})(?=\s|$|[.,;:!?])", incoming)
    if not previous_match or not incoming_match:
        return 0
    if previous_match.group(1).lower() != incoming_match.group(1).lower():
        return 0
    return incoming_match.end()


def _incremental_text_delta(accumulated: str, incoming: str) -> str:
    """Normalize providers that stream cumulative or overlapping text chunks."""
    if not incoming:
        return ""
    if not accumulated:
        return incoming
    if incoming == accumulated or accumulated.endswith(incoming):
        return ""
    if incoming.startswith(accumulated):
        return incoming[len(accumulated):]

    trimmed_accumulated = accumulated.rstrip()
    if trimmed_accumulated and incoming.startswith(trimmed_accumulated):
        delta = incoming[len(trimmed_accumulated):]
        return delta.lstrip() if accumulated[-1].isspace() else delta

    max_overlap = min(len(accumulated), len(incoming))
    for overlap in range(max_overlap, 2, -1):
        if accumulated[-overlap:] == incoming[:overlap]:
            if overlap >= 4 or re.fullmatch(r"[\w'-]{3,}", incoming[:overlap]):
                delta = incoming[overlap:]
                return delta.lstrip() if accumulated[-1].isspace() else delta

    duplicate_word_end = _is_word_duplicate(accumulated, incoming)
    if duplicate_word_end:
        delta = incoming[duplicate_word_end:]
        return delta.lstrip() if accumulated[-1].isspace() else delta

    return incoming


def _merge_tool_call_deltas(tool_calls: list[dict[str, Any]], deltas: list[Any]) -> None:
    for item in deltas:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or index < 0:
            index = len(tool_calls)
        while len(tool_calls) <= index:
            tool_calls.append({
                "id": f"call_{len(tool_calls)}",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            })
        target = tool_calls[index]
        if item.get("id"):
            target["id"] = item["id"]
        if item.get("type"):
            target["type"] = item["type"]
        function = item.get("function")
        if isinstance(function, dict):
            target_function = target.setdefault("function", {"name": "", "arguments": ""})
            if function.get("name"):
                target_function["name"] = function["name"]
            if isinstance(function.get("arguments"), str):
                target_function["arguments"] = str(target_function.get("arguments") or "") + function["arguments"]


def _parse_sse_data(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if stripped.startswith("data:"):
        return stripped[len("data:"):].strip()
    return None


class ModelProviderClient:
    def __init__(
        self,
        base_url: str,
        deployment_name: str,
        api_key: Optional[str] = None,
        request_options: Optional[dict[str, Any]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.deployment_name = deployment_name
        self.api_key = api_key
        self.request_options = request_options or {}

    async def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(
        self,
        messages: list,
        temperature: float,
        max_tokens: int,
        model_override: Optional[str],
        tools: Optional[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_override or self.deployment_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        extra_body = _default_extra_body(self.base_url)
        configured_extra_body = self.request_options.get("extra_body")
        if isinstance(configured_extra_body, dict):
            extra_body.update(configured_extra_body)
        if extra_body:
            payload.update(extra_body)

        without_parameters = self.request_options.get("omit_parameters") or []
        for parameter in without_parameters:
            payload.pop(str(parameter), None)
        return payload

    async def chat_completion(
        self,
        messages: list,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        model_override: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        stream_event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict:
        url = _chat_completions_url(self.base_url)
        payload = self._payload(messages, temperature, max_tokens, model_override, tools)
        model = str(payload.get("model") or model_override or self.deployment_name)

        if stream_event_sink:
            return await self._streaming_chat_completion(url, payload, model, stream_event_sink)

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=await self._get_headers(), json=payload)
        elapsed = int((time.monotonic() - start) * 1000)

        if response.status_code != 200:
            raw_text = response.text
            try:
                body = response.json()
            except Exception:
                body = {}
            detail = _error_detail(body, raw_text)
            return {
                "error": True,
                "error_type": _classify_error(response.status_code, detail),
                "status_code": response.status_code,
                "message": detail,
                "raw_response": raw_text,
                "latency_ms": elapsed,
            }

        body = response.json()
        choice = body.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = body.get("usage", {})

        return {
            "error": False,
            "content": msg.get("content", ""),
            "finish_reason": choice.get("finish_reason", ""),
            "tool_calls": msg.get("tool_calls"),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "latency_ms": elapsed,
            "model": body.get("model", model),
            "raw_response": body,
        }

    async def _streaming_chat_completion(
        self,
        url: str,
        payload: dict[str, Any],
        model: str,
        stream_event_sink: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        payload = {**payload, "stream": True}
        content_text = ""
        reasoning_text = ""
        tool_calls: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        finish_reason = ""
        response_model = model
        raw_events: list[dict[str, Any]] = []

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, headers=await self._get_headers(), json=payload) as response:
                if response.status_code != 200:
                    raw_bytes = await response.aread()
                    raw_text = raw_bytes.decode("utf-8", errors="replace")
                    try:
                        body = json.loads(raw_text)
                    except Exception:
                        body = {}
                    detail = _error_detail(body, raw_text)
                    elapsed = int((time.monotonic() - start) * 1000)
                    return {
                        "error": True,
                        "error_type": _classify_error(response.status_code, detail),
                        "status_code": response.status_code,
                        "message": detail,
                        "raw_response": raw_text,
                        "latency_ms": elapsed,
                    }

                async for line in response.aiter_lines():
                    data = _parse_sse_data(line)
                    if data is None:
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        raw_events.append(event)
                    if len(raw_events) > 20:
                        raw_events.pop(0)

                    if isinstance(event.get("model"), str):
                        response_model = event["model"]
                    if isinstance(event.get("usage"), dict):
                        usage = event["usage"]

                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        delta = choice.get("message") if isinstance(choice.get("message"), dict) else {}

                    reasoning_delta = _incremental_text_delta(reasoning_text, _reasoning_delta(delta))
                    if reasoning_delta:
                        reasoning_text += reasoning_delta
                        stream_event_sink({"type": "reasoning_delta", "delta": reasoning_delta})

                    content_delta = _incremental_text_delta(content_text, _text_delta(delta.get("content")))
                    if content_delta:
                        content_text += content_delta
                        stream_event_sink({"type": "content_delta", "delta": content_delta})

                    streamed_tool_calls = delta.get("tool_calls")
                    if isinstance(streamed_tool_calls, list):
                        _merge_tool_call_deltas(tool_calls, streamed_tool_calls)

        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "error": False,
            "content": content_text,
            "reasoning_content": reasoning_text,
            "finish_reason": finish_reason,
            "tool_calls": tool_calls or None,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "latency_ms": elapsed,
            "model": response_model,
            "raw_response": {
                "streamed": True,
                "event_count": len(raw_events),
                "last_events": raw_events,
            },
        }
