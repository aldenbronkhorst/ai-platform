import asyncio
import os
import time
import re
import httpx
from typing import Optional, Any
from azure.identity import DefaultAzureCredential

AZURE_AI_INFERENCE_API_VERSION = "2024-05-01-preview"
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


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


class FoundryClient:
    def __init__(
        self,
        base_url: str,
        deployment_name: str,
        api_key: Optional[str] = None,
        use_managed_identity: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.deployment_name = deployment_name
        self.api_key = api_key
        self.use_managed_identity = use_managed_identity
        self._credential: Optional[DefaultAzureCredential] = None

    async def _get_headers(self) -> dict:
        if self.api_key:
            return {
                "Content-Type": "application/json",
                "api-key": self.api_key,
            }
        if self.use_managed_identity:
            if not self._credential:
                self._credential = DefaultAzureCredential()
            token = await asyncio.to_thread(self._credential.get_token, COGNITIVE_SERVICES_SCOPE)
            return {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token.token}",
            }
        return {"Content-Type": "application/json"}

    async def chat_completion(
        self,
        messages: list,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        model_override: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> dict:
        url = f"{self.base_url}/models/chat/completions?api-version={AZURE_AI_INFERENCE_API_VERSION}"
        model = model_override or self.deployment_name
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=await self._get_headers(), json=payload)
        elapsed = int((time.monotonic() - start) * 1000)

        if response.status_code != 200:
            raw_text = response.text
            try:
                body = response.json()
                detail = body.get("error", {}).get("message", raw_text)
            except Exception:
                body = {}
                detail = raw_text
            error_type = _classify_error(response.status_code, detail)
            return {
                "error": True,
                "error_type": error_type,
                "status_code": response.status_code,
                "message": detail,
                "raw_response": raw_text,
                "latency_ms": elapsed,
            }

        body = response.json()
        choice = body.get("choices", [{}])[0]
        msg = choice.get("message", {})
        usage = body.get("usage", {})

        result = {
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
        return result
