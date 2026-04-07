"""analyze_scene — Claude vision model for semantic scene understanding.

Processes all frames from a frames_asset using process_frames_in_batches(),
which determines batch sizes automatically from the model's context window
(read from the model_context_windows DB table). Writes full per-frame results
to a blob and returns only a compact summary.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from app.tools.frame_batching import process_frames_in_batches
from app.tools.generated_asset_store import read_generated_asset, write_generated_asset
from app.tools.model_registry import get_model_client, FrontierModelClient

logger = logging.getLogger(__name__)

_BATCH_PROMPT = """You are a video analysis assistant. For each image provided (in order),
return a JSON object with these fields:
- description: one-sentence summary
- objects: list of visible objects
- activities: list of actions/activities occurring
- setting: environment type (indoor/outdoor/etc.)
- mood: overall tone/atmosphere"""


async def analyze_scene(
    payload: dict[str, Any],
    progress_callback: Callable[[int, int | None, str], None] | None = None,
) -> dict[str, Any]:
    """Use a Claude vision model to semantically describe video frames.

    Batch sizes are determined automatically based on the configured model's
    context window (model_context_windows DB table), task type, and detected
    frame resolution — no frame_batch_size parameter is accepted.

    Input:
      frames_asset: str — blob URL written by extract_frames (required)
      question: str (optional) — additional question to answer per frame
      job_id: str (required)
      session_id: str (optional)

    Output:
      result_asset: str — blob URL of full per-frame analysis JSON
      summary: {frames_analysed, unique_settings, common_objects, model_used}
    """
    frames_asset: str = payload.get("frames_asset", "")
    question: str | None = payload.get("question") or None
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None

    raw = await read_generated_asset(frames_asset)
    frames: list[dict] = raw.get("frames", []) if isinstance(raw, dict) else []

    client = await get_model_client("claude-vision")
    if not isinstance(client, FrontierModelClient):
        logger.error("analyze_scene requires a frontier model client, got: %s", type(client))
        return {"error": "tool_frontier_model did not resolve to a frontier client"}

    prompt = _BATCH_PROMPT
    if question:
        prompt += f"\n\nAdditional question to answer per frame: {question}"

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
                result = next(result_iter, {"error": "no_response"})
                uris_consumed += 1
            else:
                url = frame_info.get("url", "")
                result = {"error": "no_url"}
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
        task_type="general",
    )

    # Build summary
    all_objects: list[str] = []
    settings_seen: set[str] = set()
    for r in per_frame_results:
        if "objects" in r and isinstance(r["objects"], list):
            all_objects.extend(r["objects"])
        if "setting" in r and isinstance(r["setting"], str):
            settings_seen.add(r["setting"])

    from collections import Counter
    top_objects = [obj for obj, _ in Counter(all_objects).most_common(10)]

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
        "frames_analysed": len(per_frame_results),
        "frames_with_errors": len(errors),
        "total_batches": len(per_batch_info),
        "batches": per_batch_info,
        "errors": errors,
        "frames": per_frame_results,
    }

    filename = f"analyze_scene_{uuid.uuid4().hex[:8]}.json"
    result_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="scene_analysis",
        filename=filename,
        data=full_result,
    )

    summary = {
        "frames_analysed": len(per_frame_results),
        "unique_settings": sorted(settings_seen),
        "common_objects": top_objects,
        "model_used": client.model_id,
    }

    logger.info(
        "analyze_scene: processed %d frames, wrote %s",
        len(per_frame_results), result_asset,
    )
    return {"result_asset": result_asset, "summary": summary}
