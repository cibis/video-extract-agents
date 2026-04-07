"""extract_frames tool — return keyframe image URLs from index."""
import logging
import uuid
from typing import Any

from app.db import get_keyframe_index
from app.tools.generated_asset_store import read_generated_asset, write_generated_asset

logger = logging.getLogger(__name__)


async def extract_frames(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Return keyframe images from the pre-computed keyframe index.

    Input:
      video_url: str
      job_id: str (required)
      session_id: str (optional)
      keyframe_index_asset: str (optional) — blob URL written by crew.py; queried from DB if absent
      frame_indices: list[int] (optional) — subset filter; all frames returned if omitted

    Output:
      result_asset: str — blob URL of the full frames JSON
      summary: {frames_returned, start_seconds, end_seconds}
    """
    logger.info("extract_frames: invoked with keys=%s", list(payload.keys()))
    video_url: str = payload.get("video_url", "")
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None
    keyframe_index_asset: str | None = payload.get("keyframe_index_asset") or None
    requested = payload.get("frame_indices")

    # Resolve keyframe index: prefer blob asset, fall back to DB
    if keyframe_index_asset:
        try:
            raw = await read_generated_asset(keyframe_index_asset)
            keyframe_index: list[dict] = raw if isinstance(raw, list) else raw.get("frames", [])
            logger.info(
                "extract_frames: loaded %d frames from asset %s",
                len(keyframe_index), keyframe_index_asset,
            )
        except Exception as exc:
            logger.warning(
                "extract_frames: could not read asset %s (%s), falling back to DB",
                keyframe_index_asset, exc,
            )
            keyframe_index = await get_keyframe_index(video_url) if video_url else []
    elif video_url:
        logger.info("extract_frames: keyframe_index_asset not provided, querying DB for %s", video_url)
        keyframe_index = await get_keyframe_index(video_url)
        logger.info("extract_frames: DB returned %d frames for %s", len(keyframe_index), video_url)
    else:
        keyframe_index = []

    # Apply optional frame_indices filter
    if requested is not None:
        requested_set = set(requested)
        frames = [kf for kf in keyframe_index if kf.get("frame_index") in requested_set]
    else:
        frames = keyframe_index

    result_frames = [
        {
            "index": kf.get("frame_index", i),
            "url": kf.get("frame_url", ""),
            "timestamp_seconds": kf.get("timestamp_seconds", float(i)),
        }
        for i, kf in enumerate(frames)
    ]

    # Compute effective fps from keyframe timestamps
    fps = 1.0
    if len(result_frames) >= 2:
        duration = result_frames[-1]["timestamp_seconds"] - result_frames[0]["timestamp_seconds"]
        if duration > 0:
            fps = round((len(result_frames) - 1) / duration, 3)

    # Write full result to blob — include video_url, fps, total_frames so downstream
    # tools (detect_motion, transcribe_audio, etc.) can resolve the source video without
    # requiring a separate input parameter.
    filename = f"extract_frames_{uuid.uuid4().hex[:8]}.json"
    result_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="frames",
        filename=filename,
        data={
            "video_url": video_url,
            "fps": fps,
            "total_frames": len(result_frames),
            "frames": result_frames,
        },
    )

    timestamps = [f["timestamp_seconds"] for f in result_frames]
    summary = {
        "frames_returned": len(result_frames),
        "fps": fps,
        "start_seconds": round(min(timestamps), 2) if timestamps else 0.0,
        "end_seconds": round(max(timestamps), 2) if timestamps else 0.0,
    }

    logger.info("extract_frames: wrote %d frames to %s", len(result_frames), result_asset)
    return {"result_asset": result_asset, "summary": summary}
