import time
import re
from typing import Optional, Any

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

        extra_body = self.request_options.get("extra_body")
        if isinstance(extra_body, dict):
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
    ) -> dict:
        url = _chat_completions_url(self.base_url)
        payload = self._payload(messages, temperature, max_tokens, model_override, tools)
        model = str(payload.get("model") or model_override or self.deployment_name)

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
