import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIModel, AIProvider
from app.services.key_vault import get_secret_value


DEFAULT_TRANSCRIPTION_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_TRANSCRIPTION_LANGUAGE = "en"
OPENAI_COMPATIBLE_PROVIDER_TYPE = "openai_compatible"
VOICE_TRANSCRIPTION_TASK_TYPE = "voice_transcription"
TRANSCRIPTION_MODEL_PRIORITY = (
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "whisper-1",
)
TRANSCRIPTION_MODEL_MARKERS = ("transcribe", "whisper", "asr")
SUPPORTED_AUDIO_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/mp4",
    "audio/mpeg",
    "audio/mp3",
    "audio/mpga",
    "audio/m4a",
    "audio/x-m4a",
    "audio/ogg",
    "audio/flac",
}


class SpeechTranscriptionConfigError(RuntimeError):
    pass


class SpeechTranscriptionUpstreamError(RuntimeError):
    def __init__(self, status_code: int, message: str, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class SpeechTranscriptionNoSpeechError(RuntimeError):
    def __init__(self, message: str, body: Any = None):
        super().__init__(message)
        self.body = body


@dataclass
class SpeechTranscriptionConfig:
    provider_name: str
    base_url: str
    api_key: str
    model: str


@dataclass
class SpeechTranscriptionResult:
    transcript: str
    provider: str
    latency_ms: int
    raw_response: dict[str, Any]


def _base_content_type(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _transcriptions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    if normalized.endswith("/audio/transcriptions"):
        return normalized
    return f"{normalized}/audio/transcriptions"


def _extract_transcript(body: dict[str, Any]) -> str:
    for key in ("text", "transcript", "DisplayText", "displayText"):
        text = body.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _looks_like_transcription_model(model_name: str) -> bool:
    name = model_name.lower()
    return any(marker in name for marker in TRANSCRIPTION_MODEL_MARKERS)


def _is_transcription_model(model: AIModel) -> bool:
    config = model.config_json if isinstance(model.config_json, dict) else {}
    task_type = str(config.get("task_type") or "").strip()
    return task_type == VOICE_TRANSCRIPTION_TASK_TYPE or _looks_like_transcription_model(
        model.model_name or model.display_name or ""
    )


def _transcription_model_sort_key(model: AIModel) -> tuple[int, str]:
    name = (model.model_name or model.display_name or "").lower()
    for index, preferred in enumerate(TRANSCRIPTION_MODEL_PRIORITY):
        if name == preferred:
            return (index, name)
    if "transcribe" in name:
        return (len(TRANSCRIPTION_MODEL_PRIORITY), name)
    if "whisper" in name:
        return (len(TRANSCRIPTION_MODEL_PRIORITY) + 1, name)
    if "asr" in name:
        return (len(TRANSCRIPTION_MODEL_PRIORITY) + 2, name)
    return (len(TRANSCRIPTION_MODEL_PRIORITY) + 3, name)


class SpeechTranscriptionService:
    async def _resolve_explicit_config(self) -> SpeechTranscriptionConfig | None:
        api_key = os.environ.get("VOICE_TRANSCRIPTION_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        return SpeechTranscriptionConfig(
            provider_name=os.environ.get("VOICE_TRANSCRIPTION_PROVIDER_NAME", "OpenAI"),
            base_url=os.environ.get("VOICE_TRANSCRIPTION_BASE_URL", DEFAULT_TRANSCRIPTION_BASE_URL),
            api_key=api_key,
            model=os.environ.get("VOICE_TRANSCRIPTION_MODEL", DEFAULT_TRANSCRIPTION_MODEL),
        )

    async def _resolve_provider_config(self, db: AsyncSession | None) -> SpeechTranscriptionConfig | None:
        if db is None:
            return None

        result = await db.execute(
            select(AIModel, AIProvider)
            .join(AIProvider, AIProvider.id == AIModel.provider_id)
            .where(
                AIModel.enabled == "true",
                AIProvider.enabled == "true",
                AIProvider.provider_type == OPENAI_COMPATIBLE_PROVIDER_TYPE,
            )
        )
        candidates = [
            (model, provider)
            for model, provider in result.all()
            if _is_transcription_model(model)
        ]
        if not candidates:
            return None

        model, provider = sorted(candidates, key=lambda item: _transcription_model_sort_key(item[0]))[0]
        if not provider.secret_reference:
            raise SpeechTranscriptionConfigError(
                f"AI provider '{provider.name}' has no saved API key for voice transcription."
            )
        api_key = await get_secret_value(provider.secret_reference)
        if not api_key:
            raise SpeechTranscriptionConfigError(
                f"AI provider '{provider.name}' has no readable API key for voice transcription."
            )
        return SpeechTranscriptionConfig(
            provider_name=provider.name,
            base_url=provider.base_url,
            api_key=api_key,
            model=model.model_name,
        )

    async def _resolve_config(self, db: AsyncSession | None) -> SpeechTranscriptionConfig:
        explicit = await self._resolve_explicit_config()
        if explicit:
            return explicit

        provider_config = await self._resolve_provider_config(db)
        if provider_config:
            return provider_config

        raise SpeechTranscriptionConfigError(
            "Voice transcription is not configured. Add an OpenAI-compatible provider with "
            "a transcribe, whisper, or asr model in AI Providers, or set VOICE_TRANSCRIPTION_API_KEY/OPENAI_API_KEY."
        )

    async def _post_transcription(
        self,
        config: SpeechTranscriptionConfig,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        data = {
            "model": config.model,
            "response_format": "json",
        }
        language = os.environ.get("VOICE_TRANSCRIPTION_LANGUAGE", DEFAULT_TRANSCRIPTION_LANGUAGE).strip()
        if language:
            data["language"] = language

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }
        files = {
            "file": (filename or "voice-input.wav", audio_bytes, _base_content_type(content_type) or "audio/wav"),
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(_transcriptions_url(config.base_url), headers=headers, data=data, files=files)

        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}

        if response.status_code >= 400:
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                message = str(error.get("message") or error.get("type") or "Voice transcription failed.")
            else:
                message = str(error or response.text or "Voice transcription failed.")
            raise SpeechTranscriptionUpstreamError(response.status_code, message, body)
        return body if isinstance(body, dict) else {"text": str(body)}

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        db: AsyncSession | None = None,
    ) -> SpeechTranscriptionResult:
        base_content_type = _base_content_type(content_type)
        if base_content_type not in SUPPORTED_AUDIO_CONTENT_TYPES:
            raise SpeechTranscriptionUpstreamError(
                415,
                f"Unsupported audio type for voice transcription: {content_type or 'unknown'}.",
            )

        config = await self._resolve_config(db)
        start = time.monotonic()
        body = await self._post_transcription(config, audio_bytes, filename, content_type)
        elapsed = int((time.monotonic() - start) * 1000)
        transcript = _extract_transcript(body)
        if not transcript:
            raise SpeechTranscriptionNoSpeechError("The transcription model returned no transcript.", body)

        return SpeechTranscriptionResult(
            transcript=transcript,
            provider=f"{config.provider_name}:{config.model}",
            latency_ms=elapsed,
            raw_response=body,
        )
