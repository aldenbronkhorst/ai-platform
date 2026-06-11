import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.security import api_key_auth
from app.services.speech_transcription import (
    SpeechTranscriptionConfigError,
    SpeechTranscriptionNoSpeechError,
    SpeechTranscriptionService,
    SpeechTranscriptionUpstreamError,
)

router = APIRouter(prefix="/voice", tags=["voice"])

VOICE_TRANSCRIPTION_MAX_BYTES = int(os.environ.get("VOICE_TRANSCRIPTION_MAX_BYTES", str(25 * 1024 * 1024)))
SUPPORTED_AUDIO_TYPES = {
    "audio/wav",
    "audio/x-wav",
}


def _base_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


@router.post("/transcribe")
async def transcribe_voice_input(
    file: UploadFile = File(...),
    auth=Depends(api_key_auth),
):
    content_type = _base_content_type(file.content_type)
    if content_type not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error_type": "unsupported_audio_type",
                "error_message": f"Unsupported audio type: {file.content_type or 'unknown'}",
            },
        )

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_type": "empty_audio",
                "error_message": "No audio was received from the browser.",
            },
        )
    if len(audio_bytes) > VOICE_TRANSCRIPTION_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error_type": "audio_too_large",
                "error_message": "Voice recording is too large to transcribe.",
                "max_bytes": VOICE_TRANSCRIPTION_MAX_BYTES,
            },
        )

    try:
        result = await SpeechTranscriptionService().transcribe(
            audio_bytes,
            file.filename or "voice-input.wav",
            file.content_type or "application/octet-stream",
        )
    except SpeechTranscriptionConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_type": "voice_transcription_not_configured",
                "error_message": str(exc),
            },
        ) from exc
    except SpeechTranscriptionUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error_type": "voice_transcription_failed",
                "error_message": str(exc),
                "upstream_status": exc.status_code,
            },
        ) from exc
    except SpeechTranscriptionNoSpeechError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_type": "no_speech_detected",
                "error_message": str(exc),
            },
        ) from exc

    return {
        "transcript": result.transcript,
        "provider": result.provider,
        "latency_ms": result.latency_ms,
    }
