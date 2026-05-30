import os
import time
import httpx
from typing import Optional, Any
from azure.identity import DefaultAzureCredential

AZURE_AI_INFERENCE_API_VERSION = "2024-05-01-preview"
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


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

    def _get_headers(self) -> dict:
        if self.api_key:
            return {
                "Content-Type": "application/json",
                "api-key": self.api_key,
            }
        if self.use_managed_identity:
            if not self._credential:
                self._credential = DefaultAzureCredential()
            token = self._credential.get_token(COGNITIVE_SERVICES_SCOPE)
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
            response = await client.post(url, headers=self._get_headers(), json=payload)
        elapsed = int((time.monotonic() - start) * 1000)

        if response.status_code != 200:
            detail = response.text
            try:
                detail = response.json().get("error", {}).get("message", response.text)
            except Exception:
                pass
            return {
                "error": True,
                "status_code": response.status_code,
                "message": detail,
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
        }
        return result
