import time
import re
import json
from typing import Optional, Any, Callable
from urllib.parse import urlparse

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


def _responses_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    return f"{normalized}/responses"


def _default_extra_body(base_url: str) -> dict[str, Any]:
    return {}


def _is_openai_api(base_url: str) -> bool:
    hostname = urlparse(str(base_url or "")).hostname
    if not hostname:
        return False
    normalized = hostname.lower().rstrip(".")
    return normalized == "api.openai.com" or normalized.endswith(".api.openai.com")


def _model_prefers_responses_api(model: str) -> bool:
    normalized = str(model or "").lower()
    return normalized.startswith("gpt-5.5") or "codex" in normalized


def _model_uses_completion_token_budget(model: str) -> bool:
    normalized = str(model or "").lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))


def _model_uses_fixed_sampling(model: str) -> bool:
    normalized = str(model or "").lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4", "kimi-k2.7-code"))


def _json_safe_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


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


ASSISTANT_HISTORY_METADATA_KEYS = {
    "reasoning_content",
    "reasoning",
    "thinking",
    "thought",
    "thought_signature",
}


def _assistant_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
    history: dict[str, Any] = {"role": "assistant"}
    for key in ("content", "tool_calls", "name", "audio", "refusal"):
        if key in message and (key == "content" or message[key] is not None):
            history[key] = _json_safe_copy(message[key])
    for key, value in message.items():
        if key in history or key == "role":
            continue
        if value is None:
            continue
        if key in ASSISTANT_HISTORY_METADATA_KEYS or key.endswith("_signature") or key.endswith("_content"):
            history[key] = _json_safe_copy(value)
    if "content" not in history:
        history["content"] = None
    return history


def _responses_message(role: str, content: Any) -> dict[str, Any] | None:
    text = _text_delta(content)
    if not text:
        return None
    if role == "tool":
        return None
    return {"role": role, "content": text}


def _responses_function_call(call: dict[str, Any]) -> dict[str, Any] | None:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(function.get("name") or "").strip()
    if not name:
        return None
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments or {}, ensure_ascii=False, default=str)
    return {
        "type": "function_call",
        "call_id": str(call.get("id") or function.get("call_id") or ""),
        "name": name,
        "arguments": arguments,
    }


def _responses_function_output(message: dict[str, Any]) -> dict[str, Any] | None:
    call_id = str(message.get("tool_call_id") or "").strip()
    if not call_id:
        return None
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": _text_delta(message.get("content")),
    }


def _responses_tool_definition(tool: dict[str, Any]) -> dict[str, Any] | None:
    function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    name = str(function.get("name") or "").strip()
    if not name:
        return None
    return {
        "type": "function",
        "name": name,
        "description": str(function.get("description") or ""),
        "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object"},
    }


def _responses_output_text(body: dict[str, Any]) -> str:
    top_level_text = body.get("output_text")
    if isinstance(top_level_text, str) and top_level_text:
        return top_level_text

    parts: list[str] = []
    output = body.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if not isinstance(text, str):
                text = part.get("output_text") if isinstance(part.get("output_text"), str) else ""
            if text:
                parts.append(text)
    return "".join(parts)


def _responses_reasoning_text(body: dict[str, Any]) -> str:
    parts: list[str] = []
    output = body.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or "reasoning" not in str(item.get("type") or ""):
            continue
        for key in ("text", "reasoning", "reasoning_content"):
            value = item.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
        summary = item.get("summary")
        if isinstance(summary, str) and summary:
            parts.append(summary)
        elif isinstance(summary, list):
            for part in summary:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text") or part.get("summary_text") or part.get("content")
                    if isinstance(text, str) and text:
                        parts.append(text)
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") or part.get("reasoning") or part.get("content")
                    if isinstance(text, str) and text:
                        parts.append(text)
    return "".join(parts)


def _responses_tool_calls(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    calls: list[dict[str, Any]] = []
    output = body.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        arguments = item.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {}, ensure_ascii=False, default=str)
        call_id = str(item.get("call_id") or item.get("id") or f"call_{len(calls)}")
        calls.append({
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    return calls or None


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
        for key, value in item.items():
            if key in {"index", "id", "type", "function"} or value is None:
                continue
            target[key] = _json_safe_copy(value)
        function = item.get("function")
        if isinstance(function, dict):
            target_function = target.setdefault("function", {"name": "", "arguments": ""})
            if function.get("name"):
                target_function["name"] = function["name"]
            if isinstance(function.get("arguments"), str):
                target_function["arguments"] = str(target_function.get("arguments") or "") + function["arguments"]
            for key, value in function.items():
                if key in {"name", "arguments"} or value is None:
                    continue
                target_function[key] = _json_safe_copy(value)


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
        self._responses_previous_response_id: str | None = None
        self._responses_sent_tool_outputs: set[str] = set()

    async def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _uses_responses_api(self, model: str) -> bool:
        configured_api = str(self.request_options.get("api") or "").strip().lower()
        if configured_api == "responses":
            return True
        if configured_api == "chat_completions":
            return False
        return _is_openai_api(self.base_url) and _model_prefers_responses_api(model)

    def _payload(
        self,
        messages: list,
        temperature: float,
        max_tokens: int,
        model_override: Optional[str],
        tools: Optional[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        model = str(model_override or self.deployment_name)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if _model_uses_completion_token_budget(model):
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
        if not _model_uses_fixed_sampling(model):
            payload["temperature"] = temperature
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

    def _responses_input(self, messages: list) -> tuple[list[dict[str, Any]], set[str]]:
        sent_tool_outputs: set[str] = set()
        if self._responses_previous_response_id:
            items: list[dict[str, Any]] = []
            for message in messages:
                if not isinstance(message, dict) or message.get("role") != "tool":
                    continue
                call_id = str(message.get("tool_call_id") or "").strip()
                if not call_id or call_id in self._responses_sent_tool_outputs:
                    continue
                output = _responses_function_output(message)
                if output:
                    items.append(output)
                    sent_tool_outputs.add(call_id)
            for message in messages[-3:]:
                if not isinstance(message, dict) or message.get("role") != "system":
                    continue
                system_message = _responses_message("system", message.get("content"))
                if system_message:
                    items.append(system_message)
            if items:
                return items, sent_tool_outputs

        items = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            if role == "tool":
                output = _responses_function_output(message)
                if output:
                    items.append(output)
                    sent_tool_outputs.add(output["call_id"])
                continue
            item = _responses_message(role, message.get("content"))
            if item:
                items.append(item)
            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if isinstance(call, dict):
                            function_call = _responses_function_call(call)
                            if function_call:
                                items.append(function_call)
        return items, sent_tool_outputs

    def _responses_payload(
        self,
        messages: list,
        max_tokens: int,
        model: str,
        tools: Optional[list[dict[str, Any]]],
    ) -> tuple[dict[str, Any], set[str]]:
        input_items, sent_tool_outputs = self._responses_input(messages)
        payload: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "max_output_tokens": max_tokens,
        }
        if self._responses_previous_response_id and sent_tool_outputs:
            payload["previous_response_id"] = self._responses_previous_response_id

        response_tools = [
            response_tool
            for tool in (tools or [])
            if isinstance(tool, dict)
            for response_tool in [_responses_tool_definition(tool)]
            if response_tool
        ]
        if response_tools:
            payload["tools"] = response_tools

        reasoning = self.request_options.get("reasoning")
        if isinstance(reasoning, dict):
            payload["reasoning"] = reasoning
        else:
            effort = str(self.request_options.get("reasoning_effort") or "").strip()
            if not effort and str(model).lower().startswith("gpt-5.5"):
                effort = "xhigh"
            if effort:
                payload["reasoning"] = {"effort": effort}

        include = self.request_options.get("include")
        if isinstance(include, list):
            payload["include"] = include

        extra_body = self.request_options.get("extra_body")
        if isinstance(extra_body, dict):
            payload.update(extra_body)
        return payload, sent_tool_outputs

    async def chat_completion(
        self,
        messages: list,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        model_override: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        stream_event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict:
        model = str(model_override or self.deployment_name)
        if self._uses_responses_api(model):
            if stream_event_sink:
                return await self._streaming_responses_completion(messages, max_tokens, model, tools, stream_event_sink)
            return await self._responses_completion(messages, max_tokens, model, tools)

        url = _chat_completions_url(self.base_url)
        payload = self._payload(messages, temperature, max_tokens, model_override, tools)
        model = str(payload.get("model") or model)

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
            "reasoning_content": _reasoning_delta(msg),
            "finish_reason": choice.get("finish_reason", ""),
            "tool_calls": msg.get("tool_calls"),
            "assistant_message": _assistant_message_for_history(msg),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "latency_ms": elapsed,
            "model": body.get("model", model),
            "raw_response": body,
        }

    async def _responses_completion(
        self,
        messages: list,
        max_tokens: int,
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        url = _responses_url(self.base_url)
        payload, sent_tool_outputs = self._responses_payload(messages, max_tokens, model, tools)
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=240.0) as client:
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
        response_id = body.get("id")
        if isinstance(response_id, str) and response_id:
            self._responses_previous_response_id = response_id
        self._responses_sent_tool_outputs.update(sent_tool_outputs)

        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        tool_calls = _responses_tool_calls(body)

        return {
            "error": False,
            "content": _responses_output_text(body),
            "finish_reason": "tool_calls" if tool_calls else str(body.get("status") or "completed"),
            "tool_calls": tool_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": elapsed,
            "model": body.get("model", model),
            "raw_response": body,
        }

    async def _streaming_responses_completion(
        self,
        messages: list,
        max_tokens: int,
        model: str,
        tools: Optional[list[dict[str, Any]]],
        stream_event_sink: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        url = _responses_url(self.base_url)
        payload, sent_tool_outputs = self._responses_payload(messages, max_tokens, model, tools)
        payload = {**payload, "stream": True}
        content_text = ""
        reasoning_text = ""
        output_items: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        response_id: str | None = None
        response_model = model
        status = ""
        finish_reason = ""
        raw_events: list[dict[str, Any]] = []

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=240.0) as client:
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
                    if not isinstance(event, dict):
                        continue
                    raw_events.append(event)
                    if len(raw_events) > 20:
                        raw_events.pop(0)

                    event_type = str(event.get("type") or "")
                    if event_type == "error":
                        detail = _error_detail(event, json.dumps(event, ensure_ascii=False, default=str))
                        elapsed = int((time.monotonic() - start) * 1000)
                        return {
                            "error": True,
                            "error_type": _classify_error(400, detail),
                            "status_code": 400,
                            "message": detail,
                            "raw_response": event,
                            "latency_ms": elapsed,
                        }

                    response_obj = event.get("response") if isinstance(event.get("response"), dict) else None
                    if response_obj:
                        if isinstance(response_obj.get("id"), str):
                            response_id = response_obj["id"]
                        if isinstance(response_obj.get("model"), str):
                            response_model = response_obj["model"]
                        if isinstance(response_obj.get("status"), str):
                            status = response_obj["status"]
                        if isinstance(response_obj.get("usage"), dict):
                            usage = response_obj["usage"]
                        if event_type == "response.failed":
                            detail = _error_detail(response_obj, json.dumps(response_obj, ensure_ascii=False, default=str))
                            elapsed = int((time.monotonic() - start) * 1000)
                            return {
                                "error": True,
                                "error_type": _classify_error(400, detail),
                                "status_code": 400,
                                "message": detail,
                                "raw_response": response_obj,
                                "latency_ms": elapsed,
                            }

                    if isinstance(event.get("usage"), dict):
                        usage = event["usage"]

                    if "output_text.delta" in event_type:
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            content_text += delta
                            stream_event_sink({"type": "content_delta", "delta": delta})
                        continue

                    if "reasoning" in event_type and "delta" in event_type:
                        delta = event.get("delta") or event.get("text")
                        if isinstance(delta, str) and delta:
                            reasoning_text += delta
                            stream_event_sink({"type": "reasoning_delta", "delta": delta})
                        continue

                    if event_type == "response.output_item.done":
                        item = event.get("item")
                        if isinstance(item, dict):
                            output_items.append(item)
                        continue

                    if event_type in {"response.completed", "response.incomplete", "response.failed"}:
                        finish_reason = event_type.removeprefix("response.")
                        break

        body = {"output": output_items, "status": status or finish_reason, "usage": usage, "model": response_model}
        if response_id:
            self._responses_previous_response_id = response_id
        self._responses_sent_tool_outputs.update(sent_tool_outputs)
        if not content_text:
            content_text = _responses_output_text(body)
        if not reasoning_text:
            reasoning_text = _responses_reasoning_text(body)

        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        tool_calls = _responses_tool_calls(body)
        elapsed = int((time.monotonic() - start) * 1000)

        return {
            "error": False,
            "content": content_text,
            "reasoning_content": reasoning_text,
            "finish_reason": "tool_calls" if tool_calls else (status or finish_reason or "completed"),
            "tool_calls": tool_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "latency_ms": elapsed,
            "model": response_model,
            "raw_response": {
                "streamed": True,
                "event_count": len(raw_events),
                "last_events": raw_events,
                "output": output_items,
            },
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
        assistant_metadata: dict[str, Any] = {}
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

                    reasoning_delta = _reasoning_delta(delta)
                    if reasoning_delta:
                        reasoning_text += reasoning_delta
                        stream_event_sink({"type": "reasoning_delta", "delta": reasoning_delta})

                    content_delta = _text_delta(delta.get("content"))
                    if content_delta:
                        content_text += content_delta
                        stream_event_sink({"type": "content_delta", "delta": content_delta})

                    for key, value in delta.items():
                        if key in {"content", "tool_calls", "reasoning_content", "reasoning", "thinking", "thought"}:
                            continue
                        if value is None:
                            continue
                        if key in ASSISTANT_HISTORY_METADATA_KEYS or key.endswith("_signature") or key.endswith("_content"):
                            assistant_metadata[key] = _json_safe_copy(value)

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
            "assistant_message": _assistant_message_for_history({
                "role": "assistant",
                **assistant_metadata,
                "content": content_text or None,
                "reasoning_content": reasoning_text or None,
                "tool_calls": tool_calls or None,
            }),
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
