"""Speech-to-text proxy: forwards recorded audio to the in-cluster Whisper
predictor (OpenAI-compatible /v1/audio/transcriptions) so the UI never reaches
the model service directly."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import settings

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict:
    """Transcribe an uploaded audio clip to text via the Whisper model."""
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio upload.")
    if len(data) > settings.whisper_max_upload_bytes:
        raise HTTPException(status_code=413, detail="Audio clip too large.")

    files = {
        "file": (audio.filename or "clip.webm", data, audio.content_type or "audio/webm"),
    }
    payload = {"model": settings.whisper_model}
    if settings.whisper_language:
        payload["language"] = settings.whisper_language

    try:
        async with httpx.AsyncClient(timeout=settings.whisper_http_timeout) as client:
            resp = await client.post(settings.whisper_url, data=payload, files=files)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Transcription service returned {exc.response.status_code}.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Transcription service unreachable."
        ) from exc

    return {"text": (body.get("text") or "").strip()}
