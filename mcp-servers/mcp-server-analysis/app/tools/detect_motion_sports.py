"""detect_motion_sports — sports-tuned motion event detection from a frames_asset."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Callable

import httpx

from app.tools.generated_asset_store import read_generated_asset, write_generated_asset

logger = logging.getLogger(__name__)

_DEFAULT_SENSITIVITY = 0.5


async def detect_motion_sports(
    payload: dict[str, Any],
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """Detect high-intensity sports motion events in video frames.

    Input:
      frames_asset: str — blob URL written by extract_frames (required)
      frame_batch_size: int — frames to process per batch (agent chooses based on frame count;
                              50–100 is safe for memory; use total frames for short clips)
      job_id: str (required)
      session_id: str (optional)
      sensitivity: float 0-1 (optional, default 0.5)

    Output:
      result_asset: str — blob URL of the full motion events JSON
      summary: {segments, events_count, peak_motion_score, total_event_duration_seconds}
    """
    frames_asset: str = payload.get("frames_asset", "")
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None
    frame_batch_size: int = max(1, int(payload.get("frame_batch_size", 50)))
    sensitivity = float(payload.get("sensitivity", _DEFAULT_SENSITIVITY))

    raw = await read_generated_asset(frames_asset)
    video_url: str = raw.get("video_url", "") if isinstance(raw, dict) else ""
    frames: list[dict] = raw if isinstance(raw, list) else raw.get("frames", [])

    events, peak_score, frame_records = await _compute_sports_motion_from_frames(
        frames, frame_batch_size, sensitivity, progress_callback
    )

    segments = [
        {"start_seconds": ev["start_seconds"], "end_seconds": ev["end_seconds"], "video_url": video_url}
        for ev in events
    ]

    for rec in frame_records:
        rec["segment_index"] = -1

    for seg, ev in zip(segments, events):
        indices = [
            i for i, f in enumerate(frame_records)
            if seg["start_seconds"] <= f["timestamp_seconds"] <= seg["end_seconds"]
        ]
        first = indices[0] if indices else -1
        last = indices[-1] if indices else -1
        seg["first_frame_index"] = first
        seg["last_frame_index"] = last
        ev["first_frame_index"] = first
        ev["last_frame_index"] = last
        for pos, frame_idx in enumerate(indices):
            frame_records[frame_idx]["segment_index"] = pos

    full_result = {
        "video_url": video_url,
        "peak_motion_score": round(peak_score, 3),
        "events": events,
        "segments": segments,
        "frames": frame_records,
    }

    filename = f"detect_motion_sports_{uuid.uuid4().hex[:8]}.json"
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
        "events_count": len(events),
        "peak_motion_score": round(peak_score, 3),
        "total_event_duration_seconds": round(total_duration, 2),
    }

    logger.info(
        "detect_motion_sports: wrote result to %s (%d events)", result_asset, len(events)
    )
    return {"result_asset": result_asset, "summary": summary}


async def _download_frame(url: str) -> bytes | None:
    """Download a single frame image; returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as exc:
        logger.warning("detect_motion_sports: could not download frame %s: %s", url, exc)
        return None


async def _compute_sports_motion_from_frames(
    frames: list[dict],
    frame_batch_size: int,
    sensitivity: float,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> tuple[list[dict], float, list[dict]]:
    """Compute sports-tuned optical flow motion score from pre-extracted keyframes.

    Processes frames in batches. The last decoded frame of each batch is carried
    forward as prev_gray to maintain optical flow continuity across batch boundaries.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.error("opencv-python is not installed; detect_motion_sports cannot run")
        raise RuntimeError(
            "detect_motion_sports requires opencv-python which is not installed in this container"
        )

    motion_scores: list[tuple[float, float]] = []  # (timestamp_seconds, score)
    frame_records: list[dict] = []
    prev_gray = None

    for batch_start in range(0, len(frames), frame_batch_size):
        batch = frames[batch_start : batch_start + frame_batch_size]

        frame_bytes_list = await asyncio.gather(
            *[_download_frame(f.get("url", "")) for f in batch],
            return_exceptions=True,
        )

        for frame_info, frame_bytes in zip(batch, frame_bytes_list):
            if isinstance(frame_bytes, Exception) or not frame_bytes:
                prev_gray = None  # Reset continuity on download failure
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
                mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                score = float(np.mean(mag))
                motion_scores.append((timestamp, score))
                frame_records.append({
                    "timestamp_seconds": timestamp,
                    "motion_score": round(score, 3),
                    "url": frame_info.get("url", ""),
                })

            prev_gray = gray  # Carries across batch boundaries

        processed = min(batch_start + len(batch), len(frames))
        if progress_callback is not None:
            progress_callback(processed, len(frames), "frames")

    if not motion_scores:
        return [], 0.0, []

    peak = max(s for _, s in motion_scores)
    threshold = peak * max(0.0, min(1.0, sensitivity))

    events: list[dict] = []
    in_event = False
    event_start = 0.0

    for ts, score in motion_scores:
        if score >= threshold and not in_event:
            in_event = True
            event_start = ts
        elif score < threshold and in_event:
            in_event = False
            events.append({
                "start_seconds": round(event_start, 2),
                "end_seconds": round(ts, 2),
                "type": "high_motion",
            })

    if in_event and motion_scores:
        events.append({
            "start_seconds": round(event_start, 2),
            "end_seconds": round(motion_scores[-1][0], 2),
            "type": "high_motion",
        })

    return events, peak, frame_records
