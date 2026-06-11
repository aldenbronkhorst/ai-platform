import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.key_vault import get_secret_value, key_vault_uri

DEFAULT_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "southafricanorth")
DEFAULT_SPEECH_LANGUAGE = os.environ.get("AZURE_SPEECH_LANGUAGE", "en-ZA")
DEFAULT_KEY_SECRET_NAMES = (
    "azure-speech-key",
    "model-provider-foundry-primary-key",
)
SUPPORTED_WAV_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
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
class SpeechTranscriptionResult:
    transcript: str
    provider: str
    latency_ms: int
    raw_response: dict[str, Any]


def _clean_stt_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if not endpoint:
        return ""
    if "://" not in endpoint:
        endpoint = f"https://{endpoint}"
    parsed = urlparse(endpoint)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname.endswith(".stt.speech.microsoft.com"):
        return ""
    return f"https://{hostname}"


def _speech_region_endpoint(region: str) -> str:
    region = region.strip().lower()
    return f"https://{region}.stt.speech.microsoft.com" if region else ""


def _extract_transcript(body: dict[str, Any]) -> str:
    for key in ("DisplayText", "displayText", "text"):
        text = body.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()

    for phrase in body.get("NBest") or []:
        if isinstance(phrase, dict):
            text = phrase.get("Display") or phrase.get("display") or phrase.get("Lexical")
            if isinstance(text, str) and text.strip():
                return text.strip()

    candidates: list[str] = []
    for key in ("combinedPhrases", "combined_phrases", "phrases", "recognizedPhrases", "recognized_phrases"):
        for phrase in body.get(key) or []:
            if isinstance(phrase, dict):
                text = phrase.get("text") or phrase.get("displayText") or phrase.get("DisplayText")
                if isinstance(text, str) and text.strip():
                    candidates.append(text.strip())
    return " ".join(candidates).strip()


class SpeechTranscriptionService:
    async def _resolve_stt_endpoint(self) -> str:
        endpoint = (
            os.environ.get("AZURE_SPEECH_STT_ENDPOINT")
            or os.environ.get("VOICE_TRANSCRIPTION_ENDPOINT")
            or os.environ.get("AZURE_SPEECH_ENDPOINT")
            or ""
        )
        cleaned = _clean_stt_endpoint(endpoint)
        if cleaned:
            return cleaned
        return _speech_region_endpoint(os.environ.get("AZURE_SPEECH_REGION", DEFAULT_SPEECH_REGION))

    async def _resolve_key(self) -> str:
        key = os.environ.get("AZURE_SPEECH_KEY") or os.environ.get("VOICE_TRANSCRIPTION_KEY")
        if key:
            return key

        if not key_vault_uri():
            return ""

        for secret_name in DEFAULT_KEY_SECRET_NAMES:
            try:
                secret_value = await get_secret_value(secret_name)
            except Exception:
                secret_value = ""
            if secret_value:
                return secret_value
        return ""

    async def _post_short_audio_transcription(
        self,
        stt_endpoint: str,
        key: str,
        audio_bytes: bytes,
        language: str,
    ) -> dict[str, Any]:
        url = (
            f"{stt_endpoint}/speech/recognition/conversation/cognitiveservices/v1"
            f"?language={language}&format=detailed"
        )
        headers = {
            "Accept": "application/json",
            "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
            "Ocp-Apim-Subscription-Key": key,
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, headers=headers, content=audio_bytes)

        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}

        if response.status_code >= 400:
            message = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else None
            raise SpeechTranscriptionUpstreamError(
                response.status_code,
                message or response.text or "Azure Speech transcription failed.",
                body,
            )
        return body

    async def transcribe(self, audio_bytes: bytes, filename: str, content_type: str) -> SpeechTranscriptionResult:
        base_content_type = (content_type or "").split(";", 1)[0].strip().lower()
        if base_content_type not in SUPPORTED_WAV_CONTENT_TYPES:
            raise SpeechTranscriptionUpstreamError(
                415,
                f"Azure Speech short-audio transcription expects 16 kHz WAV audio, got {content_type or 'unknown'}.",
            )

        stt_endpoint = await self._resolve_stt_endpoint()
        key = await self._resolve_key()
        if not stt_endpoint or not key:
            raise SpeechTranscriptionConfigError(
                "Azure Speech transcription is not configured. Set AZURE_SPEECH_REGION/AZURE_SPEECH_STT_ENDPOINT "
                "and AZURE_SPEECH_KEY, or provide the model-provider Foundry key in Key Vault."
            )

        start = time.monotonic()
        body = await self._post_short_audio_transcription(
            stt_endpoint,
            key,
            audio_bytes,
            os.environ.get("AZURE_SPEECH_LANGUAGE", DEFAULT_SPEECH_LANGUAGE),
        )
        elapsed = int((time.monotonic() - start) * 1000)
        transcript = _extract_transcript(body)
        if not transcript:
            status_text = body.get("RecognitionStatus")
            suffix = f" ({status_text})" if status_text else ""
            raise SpeechTranscriptionNoSpeechError(
                f"Azure Speech returned no transcript{suffix}.",
                body,
            )

        return SpeechTranscriptionResult(
            transcript=transcript,
            provider="azure_speech_short_audio",
            latency_ms=elapsed,
            raw_response=body,
        )
