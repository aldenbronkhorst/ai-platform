import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.models import AIModel, AIProvider
from app.services.speech_transcription import (
    SpeechTranscriptionConfigError,
    SpeechTranscriptionNoSpeechError,
    SpeechTranscriptionResult,
    SpeechTranscriptionService,
    _extract_transcript,
    _transcriptions_url,
)
from tests.conftest import TestingSessionLocal

client = TestClient(app)


def test_transcriptions_url_normalizes_base_url():
    assert _transcriptions_url("https://api.openai.com/v1/") == "https://api.openai.com/v1/audio/transcriptions"
    assert (
        _transcriptions_url("https://api.z.ai/api/paas/v4")
        == "https://api.z.ai/api/paas/v4/audio/transcriptions"
    )
    assert (
        _transcriptions_url("https://api.z.ai/api/paas/v4/chat/completions")
        == "https://api.z.ai/api/paas/v4/audio/transcriptions"
    )
    assert (
        _transcriptions_url("https://api.z.ai/api/paas/v4/audio/transcriptions")
        == "https://api.z.ai/api/paas/v4/audio/transcriptions"
    )


def test_extract_transcript_from_openai_audio_response():
    assert _extract_transcript({"text": "Hello world."}) == "Hello world."


@pytest.mark.asyncio
async def test_speech_transcription_service_uses_explicit_openai_audio_config(monkeypatch):
    service = SpeechTranscriptionService()
    captured = {}

    monkeypatch.setenv("VOICE_TRANSCRIPTION_API_KEY", "voice-key")
    monkeypatch.setenv("VOICE_TRANSCRIPTION_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("VOICE_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")
    monkeypatch.setenv("VOICE_TRANSCRIPTION_PROVIDER_NAME", "OpenAI")

    async def fake_post(config, audio_bytes, filename, content_type):
        captured.update({
            "provider": config.provider_name,
            "base_url": config.base_url,
            "api_key": config.api_key,
            "model": config.model,
            "audio_bytes": audio_bytes,
            "filename": filename,
            "content_type": content_type,
        })
        return {"text": "Dictated text."}

    monkeypatch.setattr(service, "_post_transcription", fake_post)

    result = await service.transcribe(b"audio", "voice.wav", "audio/wav")

    assert result.transcript == "Dictated text."
    assert result.provider == "OpenAI:gpt-4o-transcribe"
    assert captured == {
        "provider": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key": "voice-key",
        "model": "gpt-4o-transcribe",
        "audio_bytes": b"audio",
        "filename": "voice.wav",
        "content_type": "audio/wav",
    }


@pytest.mark.asyncio
async def test_speech_transcription_service_selects_enabled_transcription_model_from_provider(monkeypatch):
    service = SpeechTranscriptionService()
    captured = {}
    monkeypatch.delenv("VOICE_TRANSCRIPTION_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def fake_get_secret(secret_name):
        return "provider-key"

    async def fake_post(config, audio_bytes, filename, content_type):
        captured.update({
            "provider": config.provider_name,
            "base_url": config.base_url,
            "api_key": config.api_key,
            "model": config.model,
        })
        return {"text": "Provider dictated text."}

    monkeypatch.setattr("app.services.speech_transcription.get_secret_value", fake_get_secret)
    monkeypatch.setattr(service, "_post_transcription", fake_post)

    async with TestingSessionLocal() as session:
        provider_name = f"OpenAI Voice {uuid.uuid4()}"
        provider = AIProvider(
            id=uuid.uuid4(),
            name=provider_name,
            provider_type="openai_compatible",
            base_url="https://api.openai.com/v1",
            auth_type="key_vault_secret",
            secret_reference="model-provider-openai-api-key",
            enabled="true",
            capabilities={},
        )
        session.add(provider)
        await session.flush()
        session.add_all([
            AIModel(
                id=uuid.uuid4(),
                provider_id=provider.id,
                display_name="GPT-4o mini",
                model_name="gpt-4o-mini",
                deployment_name="gpt-4o-mini",
                enabled="true",
            ),
            AIModel(
                id=uuid.uuid4(),
                provider_id=provider.id,
                display_name="GPT-4o Transcribe",
                model_name="gpt-4o-transcribe",
                deployment_name="gpt-4o-transcribe",
                enabled="true",
            ),
        ])
        await session.flush()

        result = await service.transcribe(b"audio", "voice.wav", "audio/x-wav", db=session)

    assert result.transcript == "Provider dictated text."
    assert result.provider.endswith(":gpt-4o-transcribe")
    assert captured["api_key"] == "provider-key"
    assert captured["model"] == "gpt-4o-transcribe"


@pytest.mark.asyncio
async def test_speech_transcription_service_selects_zai_asr_model_from_provider(monkeypatch):
    service = SpeechTranscriptionService()
    captured = {}
    monkeypatch.delenv("VOICE_TRANSCRIPTION_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def fake_get_secret(secret_name):
        return "zai-key"

    async def fake_post(config, audio_bytes, filename, content_type):
        captured.update({
            "provider": config.provider_name,
            "base_url": config.base_url,
            "api_key": config.api_key,
            "model": config.model,
            "transcriptions_url": _transcriptions_url(config.base_url),
        })
        return {"text": "Z.ai dictated text."}

    monkeypatch.setattr("app.services.speech_transcription.get_secret_value", fake_get_secret)
    monkeypatch.setattr(service, "_post_transcription", fake_post)

    async with TestingSessionLocal() as session:
        provider_name = f"Z.ai Voice {uuid.uuid4()}"
        provider = AIProvider(
            id=uuid.uuid4(),
            name=provider_name,
            provider_type="openai_compatible",
            base_url="https://api.z.ai/api/paas/v4",
            auth_type="key_vault_secret",
            secret_reference="model-provider-zai-api-key",
            enabled="true",
            capabilities={},
        )
        session.add(provider)
        await session.flush()
        session.add_all([
            AIModel(
                id=uuid.uuid4(),
                provider_id=provider.id,
                display_name="GLM 5.2",
                model_name="glm-5.2",
                deployment_name="glm-5.2",
                enabled="true",
            ),
            AIModel(
                id=uuid.uuid4(),
                provider_id=provider.id,
                display_name="GLM ASR 2512",
                model_name="glm-asr-2512",
                deployment_name="glm-asr-2512",
                enabled="true",
            ),
        ])
        await session.flush()

        result = await service.transcribe(b"audio", "voice.wav", "audio/wav", db=session)

    assert result.transcript == "Z.ai dictated text."
    assert result.provider.endswith(":glm-asr-2512")
    assert captured == {
        "provider": provider_name,
        "base_url": "https://api.z.ai/api/paas/v4",
        "api_key": "zai-key",
        "model": "glm-asr-2512",
        "transcriptions_url": "https://api.z.ai/api/paas/v4/audio/transcriptions",
    }


@pytest.mark.asyncio
async def test_speech_transcription_service_requires_config(monkeypatch):
    monkeypatch.delenv("VOICE_TRANSCRIPTION_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(SpeechTranscriptionConfigError) as exc:
        await SpeechTranscriptionService().transcribe(b"audio", "voice.wav", "audio/wav")

    assert "Voice transcription is not configured" in str(exc.value)
    assert "transcribe, whisper, or asr model" in str(exc.value)


@pytest.mark.asyncio
async def test_speech_transcription_service_reports_no_speech(monkeypatch):
    service = SpeechTranscriptionService()
    monkeypatch.setenv("VOICE_TRANSCRIPTION_API_KEY", "voice-key")

    async def fake_post(config, audio_bytes, filename, content_type):
        return {}

    monkeypatch.setattr(service, "_post_transcription", fake_post)

    with pytest.raises(SpeechTranscriptionNoSpeechError):
        await service.transcribe(b"audio", "voice.wav", "audio/wav")


def test_voice_transcribe_rejects_non_audio_upload():
    response = client.post(
        "/voice/transcribe",
        files={"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["error_type"] == "unsupported_audio_type"


def test_voice_transcribe_returns_service_result(monkeypatch):
    async def fake_transcribe(self, audio_bytes, filename, content_type, db=None):
        assert audio_bytes == b"audio"
        assert filename == "voice.wav"
        assert content_type == "audio/wav"
        assert db is not None
        return SpeechTranscriptionResult(
            transcript="hello from the mic",
            provider="OpenAI:gpt-4o-transcribe",
            latency_ms=12,
            raw_response={"text": "hello from the mic"},
        )

    monkeypatch.setattr("app.routers.voice.SpeechTranscriptionService.transcribe", fake_transcribe)

    response = client.post(
        "/voice/transcribe",
        files={"file": ("voice.wav", io.BytesIO(b"audio"), "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json()["transcript"] == "hello from the mic"
    assert response.json()["provider"] == "OpenAI:gpt-4o-transcribe"
