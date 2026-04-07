"""detect_objects tool — open-vocabulary object detection on video frames using YOLO-World."""
import asyncio
import logging
import uuid
from typing import Any, Callable

import httpx

from app.tools.generated_asset_store import read_generated_asset, write_generated_asset
from app.tools.segment_utils import aggregate_detections_to_segments

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lazy-load YOLO-World (open-vocabulary detection). Must be called from a thread executor."""
    global _model
    if _model is None:
        from ultralytics import YOLOWorld
        _model = YOLOWorld("/app/models/yolov8s-worldv2.pt")
    return _model


async def detect_objects(
    payload: dict[str, Any],
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """
    Detect objects in video frames using YOLO-World open-vocabulary detection.

    Accepts any text description as object class — not limited to COCO classes.
    Examples: 'water', 'ocean', 'kite', 'person', 'wave', 'surfboard'.

    Input:
      frames_asset: str — blob URL written by extract_frames (required)
      frame_batch_size: int — frames to process per batch (default 50)
      job_id: str (required)
      session_id: str (optional)
      object_classes: list[str] — object descriptions to detect (e.g. ["water", "kite"])

    Output:
      result_asset: str — blob URL of the full detections JSON
      summary: {segments, classes_detected, total_detections, total_duration_seconds}
    """
    frames_asset: str = payload.get("frames_asset", "")
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None
    object_classes: list[str] = payload.get("object_classes", [])
    frame_batch_size: int = max(1, int(payload.get("frame_batch_size", 50)))

    # Load frame list from blob
    raw = await read_generated_asset(frames_asset)
    video_url: str = raw.get("video_url", "") if isinstance(raw, dict) else ""
    frames: list[dict] = raw if isinstance(raw, list) else raw.get("frames", [])

    # Deduplicate and lowercase for consistent matching; preserve list order for set_classes
    seen: set[str] = set()
    deduped_classes: list[str] = []
    for cls in object_classes:
        key = cls.lower()
        if key not in seen:
            seen.add(key)
            deduped_classes.append(cls.lower())

    all_detections: list[dict] = []
    detected_frames: list[tuple[float, list[str], float]] = []
    frame_records: list[dict] = []
    total_frames = len(frames)
    processed_count = 0

    for batch_start in range(0, len(frames), frame_batch_size):
        for frame in frames[batch_start : batch_start + frame_batch_size]:
            frame_url: str = frame.get("url", "")
            timestamp: float = float(frame.get("timestamp_seconds", 0.0))
            if not frame_url:
                continue
            objects = await _detect_in_frame(frame_url, deduped_classes)
            classes_found = [o["class"] for o in objects]
            frame_records.append({
                "timestamp_seconds": timestamp,
                "url": frame_url,
                "detection_count": len(objects),
                "detected_classes": classes_found,
            })
            if objects:
                all_detections.append({"frame_url": frame_url, "timestamp_seconds": timestamp, "objects": objects})
                max_conf = max(o["confidence"] for o in objects)
                detected_frames.append((timestamp, classes_found, max_conf))
            processed_count += 1
            if progress_callback is not None:
                progress_callback(processed_count, total_frames, "frames")

    segments = aggregate_detections_to_segments(detected_frames)

    for rec in frame_records:
        rec["segment_index"] = -1

    for seg in segments:
        seg["video_url"] = video_url
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
        "object_classes": object_classes,
        "detections": all_detections,
        "segments": segments,
        "frames": frame_records,
    }

    filename = f"detect_objects_{uuid.uuid4().hex[:8]}.json"
    result_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="detections",
        filename=filename,
        data=full_result,
    )

    classes_detected = sorted({cls for seg in segments for cls in seg.get("classes", [])})
    total_duration = sum(s["end_seconds"] - s["start_seconds"] for s in segments)
    summary = {
        "segments": segments,
        "classes_detected": classes_detected,
        "total_detections": len(all_detections),
        "total_duration_seconds": round(total_duration, 2),
    }

    logger.info(
        "detect_objects: wrote result to %s (%d segments, %d frames with detections)",
        result_asset, len(segments), len(all_detections),
    )
    return {"result_asset": result_asset, "summary": summary}


async def _detect_in_frame(
    frame_url: str,
    object_classes: list[str],
) -> list[dict]:
    """Run YOLO-World inference on a single frame for the requested open-vocabulary classes."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.error("opencv-python or numpy is not installed; detect_objects cannot run")
        raise RuntimeError(
            "detect_objects requires opencv-python and numpy which are not installed in this container"
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(frame_url)
        resp.raise_for_status()
        img_data = np.frombuffer(resp.content, dtype=np.uint8)
        img = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("Could not decode image from %s; skipping frame", frame_url)
            return []

    model = _get_model()

    def _run() -> list[dict]:
        model.set_classes(object_classes)
        results = model(img, verbose=False)
        detections = []
        for result in results:
            for box in result.boxes:
                label = model.names[int(box.cls)]
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append({
                    "class": label,
                    "confidence": round(float(box.conf), 3),
                    "bbox": {
                        "x": round(x1),
                        "y": round(y1),
                        "width": round(x2 - x1),
                        "height": round(y2 - y1),
                    },
                })
        return detections

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)
