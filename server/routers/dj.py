import asyncio
import base64
import binascii
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile

from services.whisper_dual_service import whisper_dual_service
from services import log_service
from services_radio.conversation_service import conversation_service
from config import settings
from service_registry import services
from routers.deps import get_session_info, RateLimit, read_upload_limited
from routers.schemas import DJTalkRequest

router = APIRouter()

@router.post("/api/transcribe")
async def transcribe_audio(
        audio: UploadFile = File(...),
        session: dict = Depends(get_session_info),
        _rate_limit=Depends(RateLimit("transcribe"))
):
    if not audio:
        raise HTTPException(status_code=400, detail="No audio file provided")

    if not whisper_dual_service or not whisper_dual_service.models_loaded:
        log_service.error("Whisper transcription service not available")
        raise HTTPException(status_code=503, detail="Voice transcription service unavailable")

    session_id = session["session_id"]

    audio_bytes = await read_upload_limited(audio, settings.MAX_TRANSCRIBE_AUDIO_BYTES)

    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")

    result = await whisper_dual_service.transcribe_quality(audio_bytes)

    if not result:
        log_service.warning(f"{log_service.who(session_id)}: voice dictation could not be transcribed")
        raise HTTPException(status_code=500, detail="Transcription failed")

    log_service.listener(f"{log_service.who(session_id)}: dictated {result['duration']:.1f}s of voice into text")

    return {
        "text": result["text"],
        "language": result["language"],
        "confidence": result["language_probability"],
        "duration": result["duration"]
    }

@router.post("/api/dj/talk")
async def dj_talk(
        request: DJTalkRequest,
        session: dict = Depends(get_session_info),
        _rate_limit=Depends(RateLimit("dj_talk"))
):
    if not services.tts_queue_manager:
        raise HTTPException(status_code=503, detail="DJ service unavailable")

    session_id = session["session_id"]
    user = session["user"]

    if request.audio:
        try:
            audio_bytes = await asyncio.to_thread(base64.b64decode, request.audio, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid audio data")

        try:
            fast_text = await conversation_service.handle_audio_interaction(
                audio_bytes, session_id, user, is_guest=(user is None)
            )
            return {"status": "processing", "transcription": fast_text}
        except Exception as e:
            log_service.error(f"Audio handler failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to process audio")

    elif request.text:
        await conversation_service.handle_text_interaction(
            request.text, session_id, user, is_guest=(user is None)
        )
        return {"status": "processing", "transcription": request.text}

    else:
        raise HTTPException(status_code=400, detail="No input provided")
