"""extract_clip tool — extract a time-bounded clip from a video."""
import asyncio
import logging
import os
import tempfile
import uuid
from typing import Any

from app.tools.blob_helper import upload_to_blob, make_blob_path
from app.tools.generated_asset_store import append_to_clip_list

logger = logging.getLogger(__name__)


async def extract_clip(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Extract a clip from a video between start and end seconds.
    Appends the clip URL to a running clip_list blob for later merge.

    Input:
      video_url: str
      start_seconds: float
      end_seconds: float
      job_id: str (required)
      session_id: str (optional)
      clip_list_asset: str (optional) — existing clip list blob URL to append to
      video_duration_seconds: float (optional — used to clamp end to video length)
      output_name: str (optional)

    Output:
      clip_url: str — URL of the extracted clip
      clip_list_asset: str — blob URL of updated clip list (pass to next extract_clip or merge_clips)
      clips_collected: int — total clips accumulated so far
    """
    video_url = payload["video_url"]
    start = max(0.0, float(payload["start_seconds"]))
    end = float(payload["end_seconds"])
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None
    video_duration = payload.get("video_duration_seconds")
    if video_duration is not None:
        end = min(end, float(video_duration))
    output_name = payload.get("output_name", f"clip_{uuid.uuid4().hex[:8]}")
    output_name = os.path.splitext(output_name)[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, f"{output_name}.mp4")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", video_url,
            "-ss", str(start),
            "-to", str(end),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            output_path, "-y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg extract_clip failed: {stderr.decode()}")

        blob_path = make_blob_path("clips", output_name, job_id=job_id or None, session_id=session_id)
        clip_url = await upload_to_blob(output_path, blob_path)

    clip_list_asset = await append_to_clip_list(
        session_id=session_id,
        job_id=job_id,
        clip_url=clip_url,
    )

    # Read back how many clips are now in the list
    from app.tools.generated_asset_store import read_generated_asset
    clip_list: list = await read_generated_asset(clip_list_asset)

    logger.info(
        "extract_clip: extracted %s → %s (%d clips in list)", output_name, clip_url, len(clip_list)
    )
    return {
        "clip_url": clip_url,
        "clip_list_asset": clip_list_asset,
        "clips_collected": len(clip_list),
    }
