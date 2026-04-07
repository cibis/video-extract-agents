"""transcribe_audio tool — Whisper-based audio transcription."""
import asyncio
import logging
import os
import tempfile
import uuid
from typing import Any

from app.tools.generated_asset_store import write_generated_asset

logger = logging.getLogger(__name__)


async def transcribe_audio(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Transcribe audio from a video file.

    Input:
      video_url: str
      job_id: str (required)
      session_id: str (optional)
      language: str (default "en")

    Output:
      result_asset: str — blob URL of the full transcription JSON
      summary: {word_count, segment_count, duration_seconds}
    """
    video_url = payload.get("video_url", "")
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None
    language = payload.get("language", "en")

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.wav")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", video_url,
            "-vn", "-ar", "16000", "-ac", "1",
            audio_path, "-y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0 or not os.path.exists(audio_path):
            return {
                "result_asset": "",
                "summary": {"word_count": 0, "segment_count": 0, "duration_seconds": 0.0},
                "error": "Audio extraction failed",
            }

        try:
            import whisper
        except ImportError:
            logger.error("openai-whisper is not installed; transcribe_audio cannot run")
            raise RuntimeError(
                "transcribe_audio requires openai-whisper which is not installed in this container"
            )

        model = whisper.load_model("base")
        result = model.transcribe(audio_path, language=language)

    transcript_text: str = result.get("text", "")
    segments = [
        {
            "start": seg.get("start", 0.0),
            "end": seg.get("end", 0.0),
            "text": seg.get("text", ""),
        }
        for seg in result.get("segments", [])
    ]

    full_result = {
        "transcript": transcript_text,
        "segments": segments,
        "language": language,
    }

    filename = f"transcribe_audio_{uuid.uuid4().hex[:8]}.json"
    result_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="transcription",
        filename=filename,
        data=full_result,
    )

    duration_seconds = segments[-1]["end"] if segments else 0.0
    summary = {
        "word_count": len(transcript_text.split()),
        "segment_count": len(segments),
        "duration_seconds": round(duration_seconds, 2),
    }

    logger.info("transcribe_audio: wrote result to %s (%d segments)", result_asset, len(segments))
    return {"result_asset": result_asset, "summary": summary}
