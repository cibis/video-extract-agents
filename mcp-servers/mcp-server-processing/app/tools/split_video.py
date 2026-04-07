"""split_video tool — split video into equal-length segments."""
import asyncio
import os
import tempfile
from typing import Any
from app.tools.blob_helper import upload_to_blob, make_blob_path


async def split_video(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Split a video into fixed-length segments.

    Input:
      video_url: str
      segment_length_seconds: int (default 30)

    Output:
      segment_urls: list[str]
    """
    video_url = payload["video_url"]
    segment_length = int(payload.get("segment_length_seconds", 30))
    job_id: str | None = payload.get("job_id") or None
    session_id: str | None = payload.get("session_id") or None

    with tempfile.TemporaryDirectory() as tmpdir:
        output_pattern = os.path.join(tmpdir, "segment_%04d.mp4")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", video_url,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(segment_length),
            "-reset_timestamps", "1",
            output_pattern, "-y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg split_video failed: {stderr.decode()}")

        segment_files = sorted(
            f for f in os.listdir(tmpdir) if f.startswith("segment_") and f.endswith(".mp4")
        )

        segment_urls = []
        for seg_file in segment_files:
            local_path = os.path.join(tmpdir, seg_file)
            blob_path = make_blob_path("segments", seg_file.replace(".mp4", ""), job_id=job_id, session_id=session_id)
            url = await upload_to_blob(local_path, blob_path)
            segment_urls.append(url)

    return {"segment_urls": segment_urls}
