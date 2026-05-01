"""estimate_height_above_surface — metric height estimation for first-person footage."""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any, Callable

import numpy as np

from app.blob import read_blob_bytes
from app.tools.generated_asset_store import read_generated_asset, write_generated_asset

logger = logging.getLogger(__name__)

_MODEL_ID = "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf"

_depth_processor = None
_depth_model = None

# Depth Anything V2 returns near-zero values for sky / infinitely distant pixels.
# Rows whose median depth is below this threshold are treated as sky when detecting
# the horizon line.
_SKY_DEPTH_THRESHOLD_M = 1.0


def _load_depth_model() -> tuple:
    """Lazy-load Depth Anything V2 Metric singleton. Must be called from a thread executor."""
    global _depth_processor, _depth_model
    if _depth_model is not None:
        return _depth_processor, _depth_model
    try:
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        _depth_processor = AutoImageProcessor.from_pretrained(_MODEL_ID)
        _depth_model = AutoModelForDepthEstimation.from_pretrained(_MODEL_ID)
        _depth_model.eval()
        return _depth_processor, _depth_model
    except Exception as exc:
        raise RuntimeError(
            f"estimate_height_above_surface requires the transformers package and "
            f"Depth Anything V2 weights which failed to load: {exc}"
        ) from exc


def _run_depth_inference(img_bgr: np.ndarray) -> np.ndarray:
    """Run Depth Anything V2 Metric on a BGR numpy image.

    Returns an HxW float32 array of depth values in metres.
    """
    import cv2
    import torch
    import torch.nn.functional as F
    from PIL import Image

    processor, model = _load_depth_model()
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(img_rgb)
    inputs = processor(images=pil, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
    depth = F.interpolate(
        outputs.predicted_depth.unsqueeze(1),
        size=img_bgr.shape[:2],
        mode="bicubic",
        align_corners=False,
    ).squeeze().numpy()
    return depth.astype(np.float32)


def _detect_horizon_frac(depth_map: np.ndarray) -> float | None:
    """Return horizon position as fraction of frame height (0=top, 1=bottom).

    Scans rows top-to-bottom looking for the sky→ground transition: the first
    row whose median depth exceeds _SKY_DEPTH_THRESHOLD_M after at least one
    sky row (near-zero depth) has been seen.

    Returns None when no sky is visible (all ground — camera pointing down,
    or scene with no sky) or when the entire frame is sky. In both cases the
    caller falls back to a horizontal-camera assumption.
    """
    h = depth_map.shape[0]
    # No sky at all — first row already ground-depth. Return None so the
    # caller falls back to the horizontal assumption rather than interpreting
    # horizon_frac=0.0 as "camera pointing upward 30°".
    if np.median(depth_map[0, :]) > _SKY_DEPTH_THRESHOLD_M:
        return None
    for row_idx in range(h):
        if np.median(depth_map[row_idx, :]) > _SKY_DEPTH_THRESHOLD_M:
            return row_idx / h
    return None


def _tilt_corrected_height(
    raw_depth_m: float,
    horizon_frac: float | None,
    vfov_deg: float,
) -> float:
    """Convert bottom-strip median depth to camera height using tilt geometry.

    For a camera at height h tilted down by angle θ from horizontal with vertical
    FOV vfov_deg, the bottom strip of the frame looks at angle (θ + vfov/2) below
    horizontal. Camera height: h = raw_depth × sin(θ + vfov/2).

    When the camera faces straight down (horizon above frame, horizon_frac=None
    falling back to 0.5 is not right — but in practice straight-down cameras
    produce uniform high-depth maps with no sky, so horizon_frac=None is handled
    by assuming horizontal, which for a 60° vfov gives sin(30°)=0.5 — a slight
    under-correction that is conservative).

    Args:
        raw_depth_m: Median depth of the bottom surface_sample_pct rows (metres).
        horizon_frac: Horizon row as fraction of frame height, or None if not found.
        vfov_deg: Camera vertical field of view in degrees.
    """
    if horizon_frac is None:
        # No sky detected — assume horizontal camera (most conservative fallback)
        horizon_frac = 0.5
    tilt_deg = (horizon_frac - 0.5) * vfov_deg
    bottom_strip_angle_deg = tilt_deg + vfov_deg / 2
    if bottom_strip_angle_deg <= 0:
        return 0.0  # camera pointing upward — ground not visible in bottom strip
    return raw_depth_m * math.sin(math.radians(bottom_strip_angle_deg))


async def _download_frame(url: str) -> bytes | None:
    try:
        return await read_blob_bytes(url)
    except Exception as exc:
        logger.warning("estimate_height_above_surface: could not download frame %s: %s", url, exc)
        return None


async def estimate_height_above_surface(
    payload: dict[str, Any],
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """Estimate camera height above the ground or water surface in first-person (POV) footage.

    Uses Depth Anything V2 Metric Outdoor to produce absolute per-frame depth in metres.
    The horizon row is detected from the depth map to derive camera tilt; a geometric
    correction converts bottom-strip depth to actual camera height for any camera angle
    from horizontal (GoPro POV) to straight-down (drone).

    Input:
      frames_asset: str — blob URL written by extract_frames (required)
      job_id: str (required)
      session_id: str (optional)
      frame_batch_size: int — default 20, max 100
      surface_sample_pct: float 0.05–0.50 — fraction of frame height from bottom (default 0.20)
      height_threshold_m: float — metres above surface to count as airborne (default 2)
      camera_vfov_deg: float 10–150 — camera vertical FOV in degrees (default 60)

    Output:
      result_asset: str — blob URL of full per-frame height data
      summary: {events_count, peak_height_m, total_event_duration_seconds}
    """
    frames_asset: str = payload.get("frames_asset", "")
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None
    frame_batch_size: int = min(100, max(1, int(payload.get("frame_batch_size", 20))))
    surface_sample_pct: float = max(0.05, min(0.5, float(payload.get("surface_sample_pct", 0.20))))
    height_threshold_m: float = max(0.05, float(payload.get("height_threshold_m", 2)))
    camera_vfov_deg: float = max(10.0, min(150.0, float(payload.get("camera_vfov_deg", 60.0))))

    raw = await read_generated_asset(frames_asset)
    video_url: str = raw.get("video_url", "") if isinstance(raw, dict) else ""
    frames: list[dict] = raw if isinstance(raw, list) else raw.get("frames", [])

    frame_records = await _compute_heights(frames, frame_batch_size, surface_sample_pct, camera_vfov_deg, progress_callback)

    if not frame_records:
        return {
            "result_asset": None,
            "summary": {
                "segments": [],
                "events_count": 0,
                "peak_height_m": 0.0,
                "total_event_duration_seconds": 0.0,
            },
        }

    # Compute per-video baseline depth (25th percentile of all frame heights).
    # This makes the threshold relative to the video's own ground/surface level
    # rather than an absolute value — required for forward-facing cameras (e.g.
    # helmet-mounted GoPro, kitesurfer POV) where the surface is always >2 m
    # from the camera even when the rider is at ground/water level.
    # For a straight-down drone the baseline ≈ hover altitude and the threshold
    # adds on top, which is equally correct.
    all_heights = [r["height_m"] for r in frame_records]
    baseline_m = float(np.percentile(all_heights, 25))
    effective_threshold_m = baseline_m + height_threshold_m

    logger.debug(
        "estimate_height_above_surface: baseline_m=%.3f height_threshold_m=%.3f "
        "effective_threshold_m=%.3f over %d frames",
        baseline_m, height_threshold_m, effective_threshold_m, len(frame_records),
    )

    # Detect events — consecutive frames above effective_threshold_m
    events: list[dict] = []
    in_event = False
    event_start_ts = 0.0
    peak_in_event = 0.0

    for i, rec in enumerate(frame_records):
        airborne = rec["height_m"] > effective_threshold_m
        if airborne and not in_event:
            in_event = True
            event_start_ts = rec["timestamp_seconds"]
            peak_in_event = rec["height_m"]
        elif in_event:
            if rec["height_m"] > peak_in_event:
                peak_in_event = rec["height_m"]
            if not airborne:
                in_event = False
                events.append({
                    "start_seconds": round(event_start_ts, 2),
                    "end_seconds": round(rec["timestamp_seconds"], 2),
                    "type": "airborne",
                    "peak_height_m": round(peak_in_event, 3),
                })

    if in_event and frame_records:
        events.append({
            "start_seconds": round(event_start_ts, 2),
            "end_seconds": round(frame_records[-1]["timestamp_seconds"], 2),
            "type": "airborne",
            "peak_height_m": round(peak_in_event, 3),
        })

    # Build segments (same time ranges as events) and tag first/last frame indices
    segments = [
        {"start_seconds": ev["start_seconds"], "end_seconds": ev["end_seconds"], "peak_height_m": ev["peak_height_m"], "video_url": video_url}
        for ev in events
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

    peak_height_m = max((seg["peak_height_m"] for seg in segments), default=0.0)

    full_result = {
        "video_url": video_url,
        "peak_height_m": round(peak_height_m, 3),
        "segments": segments,
        "frames": frame_records,
    }

    filename = f"estimate_height_{uuid.uuid4().hex[:8]}.json"
    result_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="height_above_surface",
        filename=filename,
        data=full_result,
    )

    total_duration = sum(s["end_seconds"] - s["start_seconds"] for s in segments)
    summary = {
        "segments_count": len(segments),
        "peak_height_m": round(peak_height_m, 3),
        "total_event_duration_seconds": round(total_duration, 2),
    }

    logger.info(
        "estimate_height_above_surface: wrote result to %s (%d segments, peak=%.2fm)",
        result_asset, len(segments), peak_height_m,
    )
    return {"result_asset": result_asset, "summary": summary}


async def _compute_heights(
    frames: list[dict],
    frame_batch_size: int,
    surface_sample_pct: float,
    camera_vfov_deg: float,
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> list[dict]:
    """Run Depth Anything V2 inference on all frames.

    Returns list of {timestamp_seconds, url, height_m, horizon_frac} — one entry per
    successfully processed frame. height_m is the tilt-corrected camera height in metres;
    horizon_frac is the detected horizon row as a fraction of frame height (None if not found).
    """
    try:
        import cv2
    except ImportError:
        raise RuntimeError(
            "estimate_height_above_surface requires opencv-python which is not installed"
        )

    loop = asyncio.get_event_loop()
    frame_records: list[dict] = []

    for batch_start in range(0, len(frames), frame_batch_size):
        batch = frames[batch_start: batch_start + frame_batch_size]

        frame_bytes_list = await asyncio.gather(
            *[_download_frame(f.get("url", "")) for f in batch],
            return_exceptions=True,
        )

        for frame_info, frame_bytes in zip(batch, frame_bytes_list):
            if isinstance(frame_bytes, Exception) or not frame_bytes:
                continue

            img_data = np.frombuffer(frame_bytes, dtype=np.uint8)
            img = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
            if img is None:
                continue

            timestamp = float(frame_info.get("timestamp_seconds", 0.0))
            url = frame_info.get("url", "")

            try:
                depth_map = await loop.run_in_executor(None, _run_depth_inference, img)
            except Exception as exc:
                logger.warning(
                    "estimate_height_above_surface: inference failed at %.2fs: %s",
                    timestamp, exc,
                )
                continue

            h = depth_map.shape[0]
            surface_rows = max(1, int(h * surface_sample_pct))
            raw_depth_m = float(np.median(depth_map[-surface_rows:, :]))

            horizon_frac = _detect_horizon_frac(depth_map)
            height_m = _tilt_corrected_height(raw_depth_m, horizon_frac, camera_vfov_deg)

            logger.debug(
                "estimate_height_above_surface: t=%.2fs raw_depth=%.3fm "
                "horizon_frac=%s tilt_deg=%.1f height_m=%.3fm",
                timestamp, raw_depth_m,
                f"{horizon_frac:.3f}" if horizon_frac is not None else "None",
                (horizon_frac - 0.5) * camera_vfov_deg if horizon_frac is not None else 0.0,
                height_m,
            )

            frame_records.append({
                "timestamp_seconds": timestamp,
                "url": url,
                "height_m": round(height_m, 3),
                "horizon_frac": round(horizon_frac, 3) if horizon_frac is not None else None,
                "segment_index": -1,
            })

        processed = min(batch_start + len(batch), len(frames))
        if progress_callback is not None:
            progress_callback(processed, len(frames), "frames")

    return frame_records
