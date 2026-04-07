"""extract_clips_bulk tool — extract all segments from a video in one sequential pass."""
import asyncio
import logging
import os
import tempfile
import uuid
from typing import Any, Callable

from app.tools.blob_helper import upload_to_blob, make_blob_path
from app.tools.generated_asset_store import read_generated_asset, write_generated_asset

logger = logging.getLogger(__name__)


async def extract_clips_bulk(
    payload: dict[str, Any],
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """
    Extract all identified segments from a video sequentially in one tool call.

    Use instead of chained extract_clip calls when segments_asset is available
    from write_segments_asset. Clips are extracted one at a time and accumulated
    into a clip_list.json blob compatible with merge_clips.

    Input:
      job_id: str (required)
      session_id: str (optional)
      segments_asset: str — blob URL from write_segments_asset (preferred); each segment
                            must carry video_url identifying its source video
      segments: list[{start_seconds, end_seconds, video_url}] — inline fallback
      video_duration_seconds: float (optional — clamps end timestamps)
      output_prefix: str (optional, default "clip")

    Output:
      clip_list_asset: str — blob URL of clip_list.json (same format as extract_clip output)
      clips_extracted: int
      clip_urls: list[str]
    """
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None
    output_prefix: str = payload.get("output_prefix", "clip")
    video_duration = payload.get("video_duration_seconds")

    # Resolve segments — blob preferred, inline fallback
    segments_asset: str | None = payload.get("segments_asset")
    if segments_asset:
        segments: list = await read_generated_asset(segments_asset)
        logger.info(
            "extract_clips_bulk: loaded %d segments from blob %s",
            len(segments), segments_asset,
        )
    else:
        segments = payload.get("segments", [])
        logger.info(
            "extract_clips_bulk: using %d inline segments (no segments_asset provided)",
            len(segments),
        )

    if not segments:
        raise ValueError("No segments provided — pass segments_asset or inline segments array")

    clip_urls: list[str] = []
    total_clips = len(segments)

    for i, seg in enumerate(segments):
        start = max(0.0, float(seg["start_seconds"]))
        end = float(seg["end_seconds"])
        if video_duration is not None:
            end = min(end, float(video_duration))

        if end <= start:
            logger.warning(
                "extract_clips_bulk: skipping segment %d — end <= start (%.2f <= %.2f)",
                i, end, start,
            )
            continue

        output_name = f"{output_prefix}_{i:04d}_{uuid.uuid4().hex[:6]}"
        seg_video_url: str = seg.get("video_url") or ""
        if not seg_video_url:
            raise ValueError(
                f"Segment {i} (start={start:.2f}s, end={end:.2f}s) is missing 'video_url'. "
                "Each segment must include the source video URL. Re-run the analysis task and "
                "ensure write_segments_asset is called with segments that include 'video_url'."
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, f"{output_name}.mp4")

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i", seg_video_url,
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
                raise RuntimeError(
                    f"FFmpeg failed for segment {i} ({start:.2f}–{end:.2f}s): {stderr.decode()}"
                )

            blob_path = make_blob_path("clips", output_name, job_id=job_id or None, session_id=session_id)
            clip_url = await upload_to_blob(output_path, blob_path)

        clip_urls.append(clip_url)
        logger.info(
            "extract_clips_bulk: segment %d/%d from %s → %s (%.2f–%.2f s)",
            i + 1, total_clips, seg_video_url, clip_url, start, end,
        )
        if progress_callback is not None:
            progress_callback(len(clip_urls), total_clips, "clips")

    if not clip_urls:
        raise ValueError("No clips were extracted — all segments were invalid (end <= start)")

    # Write clip_list.json in a single write call.
    # Uses the same blob path convention as append_to_clip_list so merge_clips
    # receives an identical clip_list_asset format.
    clip_list_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="clips",
        filename="clip_list.json",
        data=clip_urls,
    )

    logger.info(
        "extract_clips_bulk: wrote %d clips to clip_list_asset %s",
        len(clip_urls), clip_list_asset,
    )
    return {
        "clip_list_asset": clip_list_asset,
        "clips_extracted": len(clip_urls),
        "clip_urls": clip_urls,
    }
