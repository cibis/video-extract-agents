"""normalize_segments — expand short segments, merge overlaps, enforce boundaries."""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import Any

from app.tools.generated_asset_store import read_generated_asset, write_generated_asset

logger = logging.getLogger(__name__)


def _expand(seg: dict, min_duration: float, video_durations: dict[str, float]) -> tuple[dict, bool]:
    """Return (expanded_seg, was_expanded).

    Expansion is centered on the original midpoint.
    start is clamped to ≥ 0 always.
    end is clamped to video_durations[video_url] when available;
    start then slides back as far as possible without going negative.
    If a video is shorter than min_duration, the segment spans [0, vid_end].
    """
    start = float(seg["start_seconds"])
    end = float(seg["end_seconds"])
    duration = end - start

    if duration >= min_duration:
        return seg, False

    center = (start + end) / 2.0
    new_start = max(0.0, center - min_duration / 2.0)
    new_end = new_start + min_duration

    video_url = seg.get("video_url", "")
    if video_url in video_durations:
        vid_end = float(video_durations[video_url])
        if new_end > vid_end:
            new_end = vid_end
            new_start = max(0.0, new_end - min_duration)

    result = dict(seg)
    result["start_seconds"] = round(new_start, 6)
    result["end_seconds"] = round(new_end, 6)
    return result, True


def _merge_overlapping(segments: list[dict]) -> tuple[list[dict], int]:
    """Merge overlapping or contiguous segments per video_url.

    Returns (merged_list, number_of_segments_removed).
    Within each video the input is sorted by start_seconds before merging.
    Extra fields from the first segment in each merged group are preserved.
    """
    by_video: dict[str, list[dict]] = defaultdict(list)
    for seg in segments:
        by_video[seg.get("video_url", "")].append(seg)

    merged: list[dict] = []
    removed = 0

    for segs in by_video.values():
        sorted_segs = sorted(segs, key=lambda s: float(s["start_seconds"]))
        current = dict(sorted_segs[0])
        for seg in sorted_segs[1:]:
            if float(seg["start_seconds"]) <= float(current["end_seconds"]):
                current["end_seconds"] = max(float(current["end_seconds"]), float(seg["end_seconds"]))
                removed += 1
            else:
                merged.append(current)
                current = dict(seg)
        merged.append(current)

    return merged, removed


async def normalize_segments(payload: dict[str, Any]) -> dict[str, Any]:
    """Expand short segments, merge overlaps, enforce start/end boundaries.

    Accepts either a segments_asset blob URL or an inline segments array.
    Writes the result as a new segments_asset blob and returns a brief summary.

    Input:
      segments_asset: str (blob URL from write_segments_asset) — OR —
      segments: list   (inline segment list)
      min_duration_seconds: float (default 3.0)
      merge_overlapping: bool (default True)
      video_durations: dict {video_url: duration_seconds} (optional, enables end clamping)
      job_id: str (required)
      session_id: str (optional)

    Output:
      segments_asset: str  — blob URL of the normalized segments list
      segments_count: int  — total segments after normalization
      expanded_count: int  — segments that were expanded
      merged_count: int    — segments removed by overlap merging
    """
    segments_asset_url: str | None = payload.get("segments_asset") or None
    inline_segments: list | None = payload.get("segments") or None
    min_duration: float = float(payload.get("min_duration_seconds") or 3.0)
    do_merge: bool = payload.get("merge_overlapping", True)
    video_durations: dict[str, float] = payload.get("video_durations") or {}
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None

    # --- Load segments ---
    if segments_asset_url:
        raw = await read_generated_asset(segments_asset_url)
        # write_segments_asset stores a plain list; fall back to {"segments": [...]}
        if isinstance(raw, list):
            segments: list[dict] = raw
        else:
            segments = raw.get("segments", [])
    elif inline_segments is not None:
        segments = list(inline_segments)
    else:
        raise ValueError("Provide either segments_asset or segments")

    if not segments:
        filename = f"normalized_segments_{uuid.uuid4().hex[:8]}.json"
        out_url = await write_generated_asset(
            session_id=session_id,
            job_id=job_id,
            data_type="segments",
            filename=filename,
            data=[],
        )
        return {"segments_asset": out_url, "segments_count": 0, "expanded_count": 0, "merged_count": 0}

    # --- Expand short segments ---
    expanded_count = 0
    expanded: list[dict] = []
    for seg in segments:
        new_seg, was_expanded = _expand(seg, min_duration, video_durations)
        expanded.append(new_seg)
        if was_expanded:
            expanded_count += 1

    # --- Merge overlapping segments ---
    merged_count = 0
    if do_merge:
        expanded, merged_count = _merge_overlapping(expanded)

    # --- Sort by video_url then start_seconds ---
    expanded.sort(key=lambda s: (s.get("video_url", ""), float(s["start_seconds"])))

    # --- Write result ---
    filename = f"normalized_segments_{uuid.uuid4().hex[:8]}.json"
    out_url = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="segments",
        filename=filename,
        data=expanded,
    )

    logger.info(
        "normalize_segments: %d → %d segments (expanded=%d, merged=%d) → %s",
        len(segments), len(expanded), expanded_count, merged_count, out_url,
    )
    return {
        "segments_asset": out_url,
        "segments_count": len(expanded),
        "expanded_count": expanded_count,
        "merged_count": merged_count,
    }
