"""merge_clips tool — concatenate clips into a final video."""
import asyncio
import logging
import os
import tempfile
import uuid
from typing import Any, Callable

from app.tools.blob_helper import upload_to_blob, make_blob_path, get_ffmpeg_accessible_url
from app.tools.generated_asset_store import read_generated_asset

logger = logging.getLogger(__name__)


async def merge_clips(
    payload: dict[str, Any],
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """
    Merge multiple video clips into a single output video.

    Input:
      clip_list_asset: str — blob URL of clip list written by extract_clip (preferred)
      clip_urls: list[str] (fallback if clip_list_asset not provided)
      job_id: str (optional)
      output_name: str (optional)

    Output:
      output_url: str
    """
    clip_list_asset: str | None = payload.get("clip_list_asset")
    job_id: str | None = payload.get("job_id") or None
    session_id: str | None = payload.get("session_id") or None
    output_name = payload.get("output_name", f"output_{uuid.uuid4().hex[:8]}")
    output_name = os.path.splitext(output_name)[0]

    if clip_list_asset:
        clip_urls: list[str] = await read_generated_asset(clip_list_asset)
    else:
        clip_urls = payload.get("clip_urls", [])

    if not clip_urls:
        raise ValueError("No clip URLs available — provide clip_list_asset or clip_urls")

    with tempfile.TemporaryDirectory() as tmpdir:
        concat_list_path = os.path.join(tmpdir, "concat.txt")
        local_clips = []

        for i, url in enumerate(clip_urls):
            clip_path = os.path.join(tmpdir, f"clip_{i:04d}.mp4")
            dl_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", get_ffmpeg_accessible_url(url), "-c", "copy", clip_path, "-y",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await dl_proc.communicate()
            local_clips.append(clip_path)
            if progress_callback is not None:
                progress_callback(i + 1, len(clip_urls), "clips")

        with open(concat_list_path, "w") as f:
            for clip_path in local_clips:
                f.write(f"file '{clip_path}'\n")

        output_path = os.path.join(tmpdir, f"{output_name}.mp4")
        merge_proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            output_path, "-y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await merge_proc.communicate()

        if merge_proc.returncode != 0:
            raise RuntimeError(f"FFmpeg merge_clips failed: {stderr.decode()}")

        blob_path = make_blob_path("outputs", output_name, job_id=job_id, session_id=session_id)
        output_url = await upload_to_blob(output_path, blob_path)

    logger.info("merge_clips: merged %d clips → %s", len(clip_urls), output_url)
    return {"output_url": output_url}
