from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


def _model_id_candidates(model: str) -> tuple[str, ...]:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return ("",)
    candidates = [normalized]
    for separator in ("/", ":"):
        if separator in normalized:
            candidates.append(normalized.rsplit(separator, 1)[-1])
    return tuple(dict.fromkeys(candidates))


def is_openai_api(base_url: str) -> bool:
    hostname = urlparse(str(base_url or "")).hostname
    if not hostname:
        return False
    normalized = hostname.lower().rstrip(".")
    return normalized == "api.openai.com" or normalized.endswith(".api.openai.com")


def model_prefers_responses_api(model: str) -> bool:
    return any(candidate.startswith("gpt-5.5") or "codex" in candidate for candidate in _model_id_candidates(model))


def model_uses_completion_token_budget(model: str) -> bool:
    return any(candidate.startswith(("gpt-5", "o1", "o3", "o4")) for candidate in _model_id_candidates(model))


def model_uses_fixed_sampling(model: str) -> bool:
    return any(candidate.startswith(("gpt-5", "o1", "o3", "o4", "kimi-k2.7-code")) for candidate in _model_id_candidates(model))


@dataclass(frozen=True)
class ProviderRequestAdapter:
    base_url: str
    request_options: dict[str, Any]

    def uses_responses_api(self, model: str) -> bool:
        configured_api = str(self.request_options.get("api") or "").strip().lower()
        if configured_api == "responses":
            return True
        if configured_api == "chat_completions":
            return False
        return is_openai_api(self.base_url) and model_prefers_responses_api(model)

    def chat_payload(
        self,
        *,
        deployment_name: str,
        messages: list,
        temperature: float,
        max_tokens: int,
        model_override: str | None,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        model = str(model_override or deployment_name)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if model_uses_completion_token_budget(model):
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
        if not model_uses_fixed_sampling(model):
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools

        extra_body: dict[str, Any] = {}
        configured_extra_body = self.request_options.get("extra_body")
        if isinstance(configured_extra_body, dict):
            extra_body.update(configured_extra_body)
        if extra_body:
            payload.update(extra_body)

        without_parameters = self.request_options.get("omit_parameters") or []
        for parameter in without_parameters:
            payload.pop(str(parameter), None)
        return payload
