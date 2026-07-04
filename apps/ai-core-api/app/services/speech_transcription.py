import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIModel, AIProvider, AIRoute
from app.services.key_vault import get_secret_value


DEFAULT_TRANSCRIPTION_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_TRANSCRIPTION_LANGUAGE = "en"
OPENAI_COMPATIBLE_PROVIDER_TYPE = "openai_compatible"
ELEVENLABS_PROVIDER_TYPE = "elevenlabs"
VOICE_TRANSCRIPTION_PROVIDER_TYPES = (OPENAI_COMPATIBLE_PROVIDER_TYPE, ELEVENLABS_PROVIDER_TYPE)
VOICE_TRANSCRIPTION_TASK_TYPE = "voice_transcription"
TRANSCRIPTION_MODEL_PRIORITY = (
    "scribe_v2",
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
    provider_type: str
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


def _elevenlabs_speech_to_text_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/speech-to-text"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/speech-to-text"
    return f"{normalized}/v1/speech-to-text"


def _provider_type_from_base_url(base_url: str) -> str:
    host = ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        host = ""
    if host == "api.elevenlabs.io" or host.endswith(".elevenlabs.io"):
        return ELEVENLABS_PROVIDER_TYPE
    return OPENAI_COMPATIBLE_PROVIDER_TYPE


def _normalize_provider_type(provider_type: str | None, base_url: str) -> str:
    raw = (provider_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return _provider_type_from_base_url(base_url)
    aliases = {
        "openai": OPENAI_COMPATIBLE_PROVIDER_TYPE,
        "openai_compatible": OPENAI_COMPATIBLE_PROVIDER_TYPE,
        "compatible": OPENAI_COMPATIBLE_PROVIDER_TYPE,
        "eleven": ELEVENLABS_PROVIDER_TYPE,
        "eleven_labs": ELEVENLABS_PROVIDER_TYPE,
        "elevenlabs": ELEVENLABS_PROVIDER_TYPE,
    }
    return aliases.get(raw, raw)


def _extract_transcript(body: dict[str, Any]) -> str:
    for key in ("text", "transcript", "DisplayText", "displayText"):
        text = body.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _upstream_error_message(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("detail") or detail.get("status")
            if message:
                return str(message)
        if isinstance(detail, str) and detail:
            return detail
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or fallback)
        if isinstance(error, str) and error:
            return error
        message = body.get("message")
        if isinstance(message, str) and message:
            return message
    return fallback


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
        base_url = os.environ.get("VOICE_TRANSCRIPTION_BASE_URL", DEFAULT_TRANSCRIPTION_BASE_URL)
        return SpeechTranscriptionConfig(
            provider_name=os.environ.get("VOICE_TRANSCRIPTION_PROVIDER_NAME", "OpenAI"),
            provider_type=_normalize_provider_type(os.environ.get("VOICE_TRANSCRIPTION_PROVIDER_TYPE"), base_url),
            base_url=base_url,
            api_key=api_key,
            model=os.environ.get("VOICE_TRANSCRIPTION_MODEL", DEFAULT_TRANSCRIPTION_MODEL),
        )

    async def _resolve_provider_config(self, db: AsyncSession | None) -> SpeechTranscriptionConfig | None:
        if db is None:
            return None

        route_result = await db.execute(
            select(AIRoute).where(AIRoute.task_type == VOICE_TRANSCRIPTION_TASK_TYPE, AIRoute.enabled == "true")
        )
        route = route_result.scalar_one_or_none()
        if route and route.primary_model_id:
            routed_result = await db.execute(
                select(AIModel, AIProvider)
                .join(AIProvider, AIProvider.id == AIModel.provider_id)
                .where(
                    AIModel.id == route.primary_model_id,
                    AIModel.enabled == "true",
                    AIProvider.enabled == "true",
                    AIProvider.provider_type.in_(VOICE_TRANSCRIPTION_PROVIDER_TYPES),
                )
            )
            routed = routed_result.first()
            if routed:
                model, provider = routed
                if _is_transcription_model(model):
                    return await self._config_from_provider_model(provider, model)

        result = await db.execute(
            select(AIModel, AIProvider)
            .join(AIProvider, AIProvider.id == AIModel.provider_id)
            .where(
                AIModel.enabled == "true",
                AIProvider.enabled == "true",
                AIProvider.provider_type.in_(VOICE_TRANSCRIPTION_PROVIDER_TYPES),
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
        return await self._config_from_provider_model(provider, model)

    async def _config_from_provider_model(self, provider: AIProvider, model: AIModel) -> SpeechTranscriptionConfig:
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
            provider_type=provider.provider_type,
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
            "Voice transcription is not configured. Add a voice transcription model in AI Providers, "
            "or set VOICE_TRANSCRIPTION_API_KEY/OPENAI_API_KEY."
        )

    async def _post_openai_transcription(
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
            message = _upstream_error_message(body, response.text or "Voice transcription failed.")
            raise SpeechTranscriptionUpstreamError(response.status_code, message, body)
        return body if isinstance(body, dict) else {"text": str(body)}

    async def _post_elevenlabs_transcription(
        self,
        config: SpeechTranscriptionConfig,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        data = {
            "model_id": config.model,
        }
        language = os.environ.get("VOICE_TRANSCRIPTION_LANGUAGE", DEFAULT_TRANSCRIPTION_LANGUAGE).strip()
        if language:
            data["language_code"] = language

        headers = {
            "Accept": "application/json",
            "xi-api-key": config.api_key,
        }
        files = {
            "file": (filename or "voice-input.wav", audio_bytes, _base_content_type(content_type) or "audio/wav"),
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                _elevenlabs_speech_to_text_url(config.base_url),
                headers=headers,
                data=data,
                files=files,
            )

        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}

        if response.status_code >= 400:
            message = _upstream_error_message(body, response.text or "Voice transcription failed.")
            raise SpeechTranscriptionUpstreamError(response.status_code, message, body)
        return body if isinstance(body, dict) else {"text": str(body)}

    async def _post_transcription(
        self,
        config: SpeechTranscriptionConfig,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        if config.provider_type == ELEVENLABS_PROVIDER_TYPE:
            return await self._post_elevenlabs_transcription(config, audio_bytes, filename, content_type)
        return await self._post_openai_transcription(config, audio_bytes, filename, content_type)

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
