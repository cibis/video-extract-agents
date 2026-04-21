"""Token-aware frame batching for vision model calls.

Determines batch sizes based on the model's context window (read from the
model_context_windows DB table on every call — no caching) and the task type.
Fetches, resizes, and base64-encodes frames before yielding batches to the
async callback so the model client does not re-fetch them.

Adapted from docs/process_frames_in_batches/code.py.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import traceback as _traceback
from typing import Awaitable, Callable

import cv2
import numpy as np

from app.blob import read_blob_bytes
from app.db import get_model_context_window

logger = logging.getLogger(__name__)

# =========================================================
# Task Profiles (from docs/process_frames_in_batches)
# =========================================================

TASK_PROFILES: dict[str, dict] = {
    "coarse_motion": {"min_height": 128, "target_frames": 40},
    "object_detection": {"min_height": 192, "target_frames": 25},
    "general": {"min_height": 224, "target_frames": 20},
    "fine_detail": {"min_height": 256, "target_frames": 12},
    "ocr": {"min_height": 320, "target_frames": 6},
}

_DEFAULT_CONTEXT_WINDOW = 128_000
_DEFAULT_SAFETY_MARGIN = 0.5
_RESERVED_TOKENS = 2_000
_FALLBACK_ASPECT_RATIO = 4 / 3


# =========================================================
# Token Estimation
# =========================================================

def _estimate_tokens_per_frame(
    width: int,
    height: int,
    bytes_per_pixel: float = 0.15,
    encoding_overhead: float = 1.33,
    tokens_per_byte: float = 0.75,
) -> int:
    raw_bytes = width * height * bytes_per_pixel
    encoded_bytes = raw_bytes * encoding_overhead
    return max(1, int(encoded_bytes * tokens_per_byte))


def _max_frames_for_budget(
    context_window: int,
    safety_margin: float,
    width: int,
    height: int,
) -> int:
    usable = int(context_window * (1 - safety_margin)) - _RESERVED_TOKENS
    if usable <= 0:
        return 0
    tpf = _estimate_tokens_per_frame(width, height)
    return max(1, usable // tpf)


# =========================================================
# Resolution Picker
# =========================================================

def _pick_optimal_resolution(
    context_window: int,
    safety_margin: float,
    task_type: str,
    aspect_ratio: float,
) -> tuple[int, int, int]:
    """Return (width, height, max_frames) for the given model budget and task."""
    profile = TASK_PROFILES.get(task_type, TASK_PROFILES["general"])
    candidates = [128, 160, 192, 224, 256, 320, 384, 480]
    candidates = [h for h in candidates if h >= profile["min_height"]]

    best: tuple[float, int, int, int] | None = None
    # Minimum frames per batch to avoid degenerate single-frame API calls.
    # Resolutions that fit fewer than this are skipped in the first pass;
    # the fallback below handles the case where no candidate meets the threshold.
    _MIN_FRAMES_PER_BATCH = 3

    for h in candidates:
        w = int(h * aspect_ratio)
        frames = _max_frames_for_budget(context_window, safety_margin, w, h)
        if frames <= 0:
            continue
        # Skip resolutions that produce very small batches — they waste API quota
        # and cause the output token budget to be too low per call.
        if frames < _MIN_FRAMES_PER_BATCH:
            continue
        score = (
            0.6 * (h / max(candidates))
            + 0.4 * min(frames / profile["target_frames"], 1.0)
        )
        if best is None or score > best[0]:
            best = (score, w, h, frames)

    if best is None:
        # No candidate fits _MIN_FRAMES_PER_BATCH — fall back to the lowest resolution
        h = min(candidates)
        w = int(h * aspect_ratio)
        frames = _max_frames_for_budget(context_window, safety_margin, w, h)
        return w, h, max(1, frames)

    _, w, h, frames = best
    return w, h, frames


# =========================================================
# Aspect Ratio Detection
# =========================================================

async def _detect_aspect_ratio(image_urls: list[str]) -> float:
    """Fetch the first decodable frame and return its width/height ratio.

    Falls back to 4:3 if no frame can be decoded.
    """
    for url in image_urls:
        if not url:
            continue
        try:
            data = await read_blob_bytes(url)
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None and img.shape[0] > 0:
                h, w = img.shape[:2]
                return w / h
        except Exception as exc:
            logger.debug("aspect ratio detection failed for %s: %s", url, exc)
    logger.warning("Could not detect aspect ratio from any frame; using %.4f fallback", _FALLBACK_ASPECT_RATIO)
    return _FALLBACK_ASPECT_RATIO


# =========================================================
# Image Fetch + Resize
# =========================================================

async def _fetch_resize_to_data_uri(
    url: str,
    target_size: tuple[int, int],
    jpeg_quality: int = 75,
) -> tuple[str | None, str | None]:
    """Download url, resize to target_size, return (JPEG data URI, error_detail).

    error_detail is None on success; data_uri is None on failure.
    """
    try:
        data = await read_blob_bytes(url)
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("Could not decode image from %s", url)
            return None, "Could not decode image data"

        img = cv2.resize(img, target_size, interpolation=cv2.INTER_LINEAR)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not ok:
            logger.warning("cv2.imencode failed for %s", url)
            return None, "cv2.imencode failed"

        b64 = base64.b64encode(bytes(buf)).decode()
        return f"data:image/jpeg;base64,{b64}", None
    except Exception as exc:
        logger.warning("_fetch_resize_to_data_uri(%s) failed: %s", url, exc)
        return None, f"{type(exc).__name__}: {exc}\n{_traceback.format_exc()}"


# =========================================================
# Main Pipeline
# =========================================================

async def process_frames_in_batches(
    image_urls: list[str],
    model_name: str,
    callback: Callable[[list[str], dict], Awaitable[None]],
    overlap_frames: int = 0,
    target_frames_per_batch: int = 30,
    task_type: str = "general",
) -> None:
    """Token-aware async frame batching pipeline.

    1. Reads model context window + safety margin from model_context_windows DB table.
    2. Detects aspect ratio from the first available frame.
    3. Picks optimal resolution for the task type.
    4. Splits image_urls into safe batches (with optional overlap).
    5. Fetches + resizes each image to a JPEG data URI in parallel.
    6. Calls the async callback with (data_uris, metadata) for each batch.

    Args:
        image_urls: Frame image URLs to process (may include empty strings).
        model_name: LiteLLM model string (e.g. "anthropic/claude-opus-4-6").
        callback: Async function receiving (data_uris: list[str], metadata: dict).
                  data_uris contains only successfully fetched frames.
        overlap_frames: Frames shared between consecutive batches (default 0).
        target_frames_per_batch: Desired max frames per batch (default 30).
        task_type: coarse_motion / object_detection / general / fine_detail / ocr.
    """
    if not image_urls:
        return

    context_window, safety_margin = await get_model_context_window(model_name)

    aspect_ratio = await _detect_aspect_ratio(image_urls)

    width, height, max_frames = _pick_optimal_resolution(
        context_window=context_window,
        safety_margin=safety_margin,
        task_type=task_type,
        aspect_ratio=aspect_ratio,
    )

    frames_per_batch = min(target_frames_per_batch, max_frames)
    if frames_per_batch <= 0:
        raise ValueError(
            f"No frames fit within context window for model {model_name!r} "
            f"(context_window={context_window}, resolution={width}×{height})."
        )

    step = max(1, frames_per_batch - overlap_frames)
    total = len(image_urls)
    batch_index = 0

    logger.info(
        "process_frames_in_batches: model=%s context=%d safety=%.1f "
        "resolution=%dx%d max_frames=%d frames_per_batch=%d task=%s total_urls=%d",
        model_name, context_window, safety_margin,
        width, height, max_frames, frames_per_batch, task_type, total,
    )

    for start in range(0, total, step):
        end = min(start + frames_per_batch, total)
        batch_urls = image_urls[start:end]

        # Fetch + resize in parallel; collect (data_uri, error_detail) pairs
        fetch_results = await asyncio.gather(
            *[_fetch_resize_to_data_uri(u, (width, height)) for u in batch_urls]
        )
        data_uris = [d for d, _ in fetch_results if d is not None]
        # Map URL → error detail for frames that failed to fetch
        fetch_errors: dict[str, str] = {
            batch_urls[i]: err
            for i, (d, err) in enumerate(fetch_results)
            if d is None and err is not None
        }

        if not data_uris:
            logger.warning(
                "process_frames_in_batches: batch %d (frames %d-%d) — all frames failed, skipping",
                batch_index, start, end,
            )
            batch_index += 1
            if end >= total:
                break
            continue

        metadata = {
            "batch_index": batch_index,
            "start_frame": start,
            "end_frame": end,
            "frames_in_batch": len(data_uris),
            "resolution": (width, height),
            "max_frames_allowed": max_frames,
            "task_type": task_type,
            "fetch_errors": fetch_errors,
        }

        await callback(data_uris, metadata)

        batch_index += 1
        if end >= total:
            break
