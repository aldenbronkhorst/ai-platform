import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.speech_transcription import (
    SpeechTranscriptionConfigError,
    SpeechTranscriptionService,
    _clean_stt_endpoint,
    _extract_transcript,
)

client = TestClient(app)


def test_clean_stt_endpoint_accepts_speech_stt_host():
    assert (
        _clean_stt_endpoint("https://southafricanorth.stt.speech.microsoft.com/")
        == "https://southafricanorth.stt.speech.microsoft.com"
    )


def test_clean_stt_endpoint_rejects_substring_host_spoofing():
    assert _clean_stt_endpoint("https://southafricanorth.stt.speech.microsoft.com.evil.example") == ""
    assert _clean_stt_endpoint("https://evil.example/path/.stt.speech.microsoft.com") == ""


def test_extract_transcript_from_short_audio_response():
    assert _extract_transcript({"DisplayText": "Hello world."}) == "Hello world."


@pytest.mark.asyncio
async def test_speech_transcription_service_posts_audio_and_parses_response(monkeypatch):
    service = SpeechTranscriptionService()
    captured = {}

    monkeypatch.setenv("AZURE_SPEECH_STT_ENDPOINT", "https://southafricanorth.stt.speech.microsoft.com")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "speech-key")

    async def fake_post(endpoint, key, audio_bytes, language):
        captured.update({
            "endpoint": endpoint,
            "key": key,
            "audio_bytes": audio_bytes,
            "language": language,
        })
        return {"DisplayText": "Dictated text."}

    monkeypatch.setattr(service, "_post_short_audio_transcription", fake_post)

    result = await service.transcribe(b"audio", "voice.wav", "audio/wav")

    assert result.transcript == "Dictated text."
    assert result.provider == "azure_speech_short_audio"
    assert captured == {
        "endpoint": "https://southafricanorth.stt.speech.microsoft.com",
        "key": "speech-key",
        "audio_bytes": b"audio",
        "language": "en-ZA",
    }


@pytest.mark.asyncio
async def test_speech_transcription_service_accepts_x_wav_uploads(monkeypatch):
    service = SpeechTranscriptionService()

    monkeypatch.setenv("AZURE_SPEECH_STT_ENDPOINT", "https://southafricanorth.stt.speech.microsoft.com")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "speech-key")

    async def fake_post(endpoint, key, audio_bytes, language):
        return {"DisplayText": "Dictated text."}

    monkeypatch.setattr(service, "_post_short_audio_transcription", fake_post)

    result = await service.transcribe(b"audio", "voice.wav", "audio/x-wav")

    assert result.transcript == "Dictated text."


@pytest.mark.asyncio
async def test_speech_transcription_service_requires_endpoint_and_key(monkeypatch):
    monkeypatch.delenv("AZURE_SPEECH_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_STT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    monkeypatch.delenv("VOICE_TRANSCRIPTION_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("VOICE_TRANSCRIPTION_KEY", raising=False)
    monkeypatch.setattr("app.services.speech_transcription.key_vault_uri", lambda: "")

    with pytest.raises(SpeechTranscriptionConfigError):
        await SpeechTranscriptionService().transcribe(b"audio", "voice.wav", "audio/wav")


def test_voice_transcribe_rejects_non_audio_upload():
    response = client.post(
        "/voice/transcribe",
        files={"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"]["error_type"] == "unsupported_audio_type"


def test_voice_transcribe_returns_service_result(monkeypatch):
    class FakeResult:
        transcript = "hello from the mic"
        provider = "test-provider"
        latency_ms = 12

    async def fake_transcribe(self, audio_bytes, filename, content_type):
        assert audio_bytes == b"audio"
        assert filename == "voice.wav"
        assert content_type == "audio/wav"
        return FakeResult()

    monkeypatch.setattr("app.routers.voice.SpeechTranscriptionService.transcribe", fake_transcribe)

    response = client.post(
        "/voice/transcribe",
        files={"file": ("voice.wav", io.BytesIO(b"audio"), "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json()["transcript"] == "hello from the mic"
