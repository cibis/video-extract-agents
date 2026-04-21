"""segment_utils — shared helper for aggregating per-frame detections into time segments."""
from __future__ import annotations


def aggregate_detections_to_segments(
    detected_frames: list[tuple[float, list[str], float]],
    gap_seconds: float = 2.0,
) -> list[dict]:
    """Merge consecutive detected frame timestamps into contiguous time segments.

    Args:
        detected_frames: List of (timestamp_seconds, classes, confidence) tuples
                         for every frame where the target was detected.
        gap_seconds: Maximum gap between consecutive detections to be merged
                     into the same segment (default 2.0 s).

    Returns:
        List of {start_seconds, end_seconds, classes, max_confidence} dicts.
    """
    if not detected_frames:
        return []

    sorted_frames = sorted(detected_frames, key=lambda x: x[0])

    segments: list[dict] = []
    seg_start = sorted_frames[0][0]
    seg_end = sorted_frames[0][0]
    seg_classes: set[str] = set(sorted_frames[0][1])
    seg_max_conf = sorted_frames[0][2]

    for ts, classes, conf in sorted_frames[1:]:
        if ts - seg_end <= gap_seconds:
            seg_end = ts
            seg_classes.update(classes)
            seg_max_conf = max(seg_max_conf, conf)
        else:
            segments.append({
                "start_seconds": round(seg_start, 2),
                "end_seconds": round(seg_end, 2),
                "classes": sorted(seg_classes),
                "max_confidence": round(seg_max_conf, 3),
            })
            seg_start = ts
            seg_end = ts
            seg_classes = set(classes)
            seg_max_conf = conf

    segments.append({
        "start_seconds": round(seg_start, 2),
        "end_seconds": round(seg_end, 2),
        "classes": sorted(seg_classes),
        "max_confidence": round(seg_max_conf, 3),
    })

    return segments
