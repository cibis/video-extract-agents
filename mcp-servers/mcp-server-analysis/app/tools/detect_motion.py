"""detect_motion tool — compute motion score from a frames_asset using optical flow."""
import asyncio
import logging
import uuid
from typing import Any, Callable

from app.tools.generated_asset_store import read_generated_asset, write_generated_asset

logger = logging.getLogger(__name__)


async def detect_motion(
    payload: dict[str, Any],
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """
    Detect motion in video frames using optical flow (OpenCV).

    Input:
      frames_asset: str — blob URL written by extract_frames (required)
      frame_batch_size: int — frames to process per batch (agent chooses based on frame count;
                              50–100 is safe for memory; use total frames for short clips)
      job_id: str (required)
      session_id: str (optional)

    Output:
      result_asset: str — blob URL of the full motion result JSON
      summary: {segments, motion_score, high_motion_segments_count, total_motion_duration_seconds}
    """
    frames_asset: str = payload.get("frames_asset", "")
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None
    frame_batch_size: int = max(1, int(payload.get("frame_batch_size", 50)))

    raw = await read_generated_asset(frames_asset)
    video_url: str = raw.get("video_url", "") if isinstance(raw, dict) else ""
    frames: list[dict] = raw if isinstance(raw, list) else raw.get("frames", [])

    total_frames = len(frames)
    motion_score, high_motion, frame_records = await _compute_motion_from_frames(
        frames, frame_batch_size, total_frames, progress_callback
    )

    segments = [
        {"start_seconds": seg["start"], "end_seconds": seg["end"], "video_url": video_url}
        for seg in high_motion
    ]

    for rec in frame_records:
        rec["segment_index"] = -1

    for seg in segments:
        indices = [
            i for i, f in enumerate(frame_records)
            if seg["start_seconds"] <= f["timestamp_seconds"] <= seg["end_seconds"]
        ]
        seg["first_frame_index"] = indices[0] if indices else -1
        seg["last_frame_index"] = indices[-1] if indices else -1
        for pos, frame_idx in enumerate(indices):
            frame_records[frame_idx]["segment_index"] = pos

    full_result = {
        "video_url": video_url,
        "motion_score": round(motion_score, 3),
        "high_motion_segments": high_motion,
        "segments": segments,
        "frames": frame_records,
    }

    filename = f"detect_motion_{uuid.uuid4().hex[:8]}.json"
    result_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="motion",
        filename=filename,
        data=full_result,
    )

    total_duration = sum(s["end_seconds"] - s["start_seconds"] for s in segments)
    summary = {
        "segments": segments,
        "motion_score": round(motion_score, 3),
        "high_motion_segments_count": len(segments),
        "total_motion_duration_seconds": round(total_duration, 2),
    }

    logger.info("detect_motion: wrote result to %s (%d segments)", result_asset, len(segments))
    return {"result_asset": result_asset, "summary": summary}


async def _download_frame(url: str) -> bytes | None:
    """Download a single frame image; returns None on failure.

    Uses the Azure Storage SDK for blob URLs (authenticated) so that frames
    in accounts with anonymous access disabled are still readable.
    """
    try:
        from app.blob import read_blob_bytes
        return await read_blob_bytes(url)
    except Exception as exc:
        logger.warning("detect_motion: could not download frame %s: %s", url, exc)
        return None


async def _compute_motion_from_frames(
    frames: list[dict],
    frame_batch_size: int,
    total_frames: int = 0,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> tuple[float, list, list[dict]]:
    """Compute optical flow motion score from pre-extracted keyframes.

    Processes frames in batches of frame_batch_size.
    The last decoded frame of each batch is carried forward as prev_gray to
    maintain optical flow continuity across batch boundaries.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.error("opencv-python is not installed; detect_motion cannot run")
        raise RuntimeError(
            "detect_motion requires opencv-python which is not installed in this container"
        )

    motion_scores: list[tuple[float, float]] = []  # (timestamp_seconds, score)
    frame_records: list[dict] = []
    prev_gray = None

    for batch_start in range(0, len(frames), frame_batch_size):
        batch = frames[batch_start : batch_start + frame_batch_size]

        # Download batch frames concurrently
        frame_bytes_list = await asyncio.gather(
            *[_download_frame(f.get("url", "")) for f in batch],
            return_exceptions=True,
        )

        for frame_info, frame_bytes in zip(batch, frame_bytes_list):
            if isinstance(frame_bytes, Exception) or not frame_bytes:
                prev_gray = None  # Reset flow continuity on download failure
                continue

            img_data = np.frombuffer(frame_bytes, dtype=np.uint8)
            img = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
            if img is None:
                prev_gray = None
                continue

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            timestamp = float(frame_info.get("timestamp_seconds", 0.0))

            if prev_gray is not None:
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
                )
                magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
                score = float(np.mean(magnitude))
                motion_scores.append((timestamp, score))
                frame_records.append({
                    "timestamp_seconds": timestamp,
                    "motion_score": round(score, 3),
                    "url": frame_info.get("url", ""),
                })

            prev_gray = gray  # Carries across batch boundaries

        processed_so_far = batch_start + len(batch)
        if progress_callback is not None:
            progress_callback(processed_so_far, total_frames or None, "frames")

    if not motion_scores:
        return 0.0, [], []

    scores_only = [s for _, s in motion_scores]
    avg_motion = float(np.mean(scores_only))
    normalised = min(avg_motion / 20.0, 1.0)

    high_threshold = avg_motion * 1.5
    segments: list[dict] = []
    in_segment = False
    seg_start = 0.0

    for ts, score in motion_scores:
        if score > high_threshold and not in_segment:
            seg_start = ts
            in_segment = True
        elif score <= high_threshold and in_segment:
            segments.append({"start": round(seg_start, 2), "end": round(ts, 2)})
            in_segment = False

    if in_segment and motion_scores:
        segments.append({"start": round(seg_start, 2), "end": round(motion_scores[-1][0], 2)})

    return normalised, segments, frame_records
