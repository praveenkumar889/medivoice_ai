import os
import io
import logging
import httpx
from openai import AsyncOpenAI
from services.supabase_service import save_interaction, get_patient_by_phone

logger = logging.getLogger(__name__)

async def process_recording(
    recording_url: str,
    call_id: str,
    patient_phone: str,
    duration: int,
    booking_id: str = None
):
    """
    Download call recording asynchronously → run OpenAI Whisper ASR → save transcript.
    Runs as a background task after the call ends.
    Replaces the heavy local VibeVoice-ASR 7B model.
    """
    logger.info(f"Processing recording for call {call_id} using Whisper API")

    try:
        # Step 1: Download the recording from Vapi / Twilio via httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(recording_url, timeout=60.0)
            audio_bytes = response.content

        if not audio_bytes:
            logger.warning(f"Empty recording for call {call_id}")
            return

        # Step 2: Run OpenAI Whisper ASR
        openai_key = os.getenv("OPENAI_API_KEY")
        if not openai_key:
            logger.warning("OPENAI_API_KEY missing — saving recording URL only")
            await _save_interaction_without_transcript(
                recording_url, patient_phone, booking_id, duration
            )
            return

        openai_client = AsyncOpenAI(api_key=openai_key)
        
        # Whisper requires a file-like object with a name attribute
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "recording.wav" 

        transcription = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )

        transcript = {
            "full_text": transcription.text,
            "chunks": [], # Not requesting word-level timestamps to save latency, but schema supports it
        }

        # Step 3: Look up patient
        patient = (
            await get_patient_by_phone(patient_phone) if patient_phone else None
        )

        # Step 4: Save to Supabase interactions table
        await save_interaction({
            "patient_id":    patient["id"] if patient else None,
            "booking_id":    booking_id,
            "recording_url": recording_url,
            "transcript":    transcript,
            "duration_secs": duration,
        })

        logger.info(
            f"Whisper transcript saved for call {call_id} "
            f"({len(transcript['full_text'])} chars)"
        )

    except Exception as e:
        logger.error(
            f"Whisper processing failed for {call_id}: {e}",
            exc_info=True
        )


async def _save_interaction_without_transcript(
    recording_url, patient_phone, booking_id, duration
):
    """Fallback: save interaction record without ASR transcript."""
    patient = (
        await get_patient_by_phone(patient_phone) if patient_phone else None
    )
    await save_interaction({
        "patient_id":    patient["id"] if patient else None,
        "booking_id":    booking_id,
        "recording_url": recording_url,
        "transcript":    None,
        "duration_secs": duration,
    })
