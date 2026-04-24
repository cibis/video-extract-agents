"""detect_objects_vision — open-vocabulary object detection via Claude vision.

Processes all frames from a frames_asset using process_frames_in_batches(),
which determines batch sizes automatically from the model's context window
(read from the model_context_windows DB table). Writes full per-frame detection
results to a blob and returns only a compact summary.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from app.tools.frame_batching import process_frames_in_batches
from app.tools.generated_asset_store import read_generated_asset, write_generated_asset
from app.tools.model_registry import get_model_client, FrontierModelClient
from app.tools.segment_utils import aggregate_detections_to_segments

logger = logging.getLogger(__name__)


def _build_batch_prompt(object_descriptions: list[str]) -> str:
    targets = ", ".join(f'"{d}"' for d in object_descriptions)
    return (
        f"For each image provided (in order), detect the following objects: {targets}.\n"
        "Return a JSON array with one element per image. Each element must be an object with:\n"
        '- "detections": array of objects, each with:\n'
        '    - "object": the object description\n'
        '    - "present": true/false\n'
        '    - "confidence": 0.0-1.0\n'
        '    - "location_description": brief description of where in the frame\n'
        '    - "bbox_rough": [x1_pct, y1_pct, x2_pct, y2_pct] as percentages of image dimensions\n'
        "Return ONLY the JSON array, no extra text."
    )


async def detect_objects_vision(
    payload: dict[str, Any],
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """Use Claude vision to detect objects described in natural language across video frames.

    Batch sizes are determined automatically based on the configured model's
    context window (model_context_windows DB table), task type, and detected
    frame resolution — no frame_batch_size parameter is accepted.

    Input:
      frames_asset: str — blob URL written by extract_frames (required)
      object_descriptions: list[str] — natural language descriptions of objects to find (required)
      job_id: str (required)
      session_id: str (optional)

    Output:
      result_asset: str — blob URL of full per-frame detections JSON
      summary: {frames_analysed, objects_searched, frames_with_detections, model_used}
    """
    frames_asset: str = payload.get("frames_asset", "")
    object_descriptions: list[str] = payload.get("object_descriptions") or []
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None

    if not object_descriptions:
        return {"error": "object_descriptions is required and must be a non-empty list"}

    raw = await read_generated_asset(frames_asset)
    frames: list[dict] = raw.get("frames", []) if isinstance(raw, dict) else []

    client = await get_model_client("claude-vision")
    if not isinstance(client, FrontierModelClient):
        logger.error("detect_objects_vision requires a frontier model client, got: %s", type(client))
        return {"error": "tool_frontier_model did not resolve to a frontier client"}

    prompt = _build_batch_prompt(object_descriptions)
    image_urls = [f.get("url", "") for f in frames]
    per_frame_results: list[dict] = []
    per_batch_info: list[dict] = []
    batches_done = 0

    async def _callback(data_uris: list[str], metadata: dict) -> None:
        nonlocal batches_done
        per_batch_info.append({
            "batch_index": metadata["batch_index"],
            "frames_in_batch": metadata["frames_in_batch"],
            "resolution": list(metadata["resolution"]),
            "frame_range": [metadata["start_frame"], metadata["end_frame"]],
        })
        start = metadata["start_frame"]
        end = metadata["end_frame"]
        batch_frames = frames[start:end]
        fetch_errors: dict[str, str] = metadata.get("fetch_errors", {})

        batch_results = await client.call_vision_batch(prompt, data_uris)

        # Align results with frame metadata.
        # data_uris may be fewer than batch_frames if some frames failed to fetch;
        # those frames receive an error placeholder.
        result_iter = iter(batch_results)
        uris_consumed = 0
        for i, frame_info in enumerate(batch_frames, start=start):
            if frame_info.get("url") and uris_consumed < len(data_uris):
                result = next(result_iter, {"detections": [], "error": "no_response"})
                uris_consumed += 1
            else:
                url = frame_info.get("url", "")
                result = {"detections": [], "error": "no_url"}
                err_detail = fetch_errors.get(url)
                if err_detail:
                    result["error_detail"] = err_detail
            result["timestamp_seconds"] = frame_info.get("timestamp_seconds", 0.0)
            result["index"] = i
            per_frame_results.append(result)

        batches_done += 1
        if progress_callback is not None:
            progress_callback(batches_done, None, "batches")

    await process_frames_in_batches(
        image_urls=image_urls,
        model_name=client.model_id,
        callback=_callback,
        task_type="object_detection",
    )

    # Count frames that have at least one present detection
    frames_with_detections = sum(
        1 for r in per_frame_results
        if any(d.get("present", False) for d in r.get("detections", []))
    )

    # Build segments from frames where any detection was present
    detected_frames = [
        (
            r["timestamp_seconds"],
            [d["object"] for d in r.get("detections", []) if d.get("present")],
            max((d.get("confidence", 0.0) for d in r.get("detections", []) if d.get("present")), default=0.0),
        )
        for r in per_frame_results
        if any(d.get("present", False) for d in r.get("detections", []))
    ]
    segments = aggregate_detections_to_segments(detected_frames)

    errors = [
        {
            "frame_index": r["index"],
            "timestamp_seconds": r.get("timestamp_seconds", 0.0),
            "error_type": r["error"],
            **({"error_detail": r["error_detail"]} if "error_detail" in r else {}),
            **({"traceback": r["traceback"]} if "traceback" in r else {}),
        }
        for r in per_frame_results
        if "error" in r
    ]

    full_result = {
        "model_used": client.model_id,
        "object_descriptions": object_descriptions,
        "frames_analysed": len(per_frame_results),
        "frames_with_errors": len(errors),
        "total_batches": len(per_batch_info),
        "batches": per_batch_info,
        "errors": errors,
        "frames": per_frame_results,
    }

    filename = f"detect_objects_vision_{uuid.uuid4().hex[:8]}.json"
    result_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="object_detection_vision",
        filename=filename,
        data=full_result,
    )

    summary = {
        "frames_analysed": len(per_frame_results),
        "objects_searched": object_descriptions,
        "frames_with_detections": frames_with_detections,
        #"segments": segments,
        "model_used": client.model_id,
    }

    logger.info(
        "detect_objects_vision: processed %d frames, wrote %s",
        len(per_frame_results), result_asset,
    )
    return {"result_asset": result_asset, "summary": summary}
