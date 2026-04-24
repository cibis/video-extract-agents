"""query_asset — apply a JSONPath expression to a blob asset and return matched subset.

Avoids loading large blobs into the agent context window.  The agent sends a
targeted JSONPath expression and only the matching values are returned.
"""
from __future__ import annotations

import logging
from typing import Any
import uuid

from app.tools.generated_asset_store import read_generated_asset, write_generated_asset

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RESULTS = 50


async def query_asset(payload: dict[str, Any]) -> dict[str, Any]:
    """Download a blob asset and return a JSONPath-filtered subset.

    Input:
      blob_url: str — URL of any generated asset blob (required)
      jsonpath: str — JSONPath expression, e.g. "$.frames[*].timestamp_seconds" (required)
      max_results: int — maximum number of matched values to return (default 50)

    Output:
      matches: list — matched values (capped at max_results)
      total_matches: int — total number of matches before capping
      truncated: bool — true if total_matches > max_results
    """
    blob_url: str = payload.get("blob_url", "")
    jsonpath_expr: str = payload.get("jsonpath", "")
    max_results: int = max(1, int(payload.get("max_results", _DEFAULT_MAX_RESULTS)))

    if not blob_url:
        return {"error": "blob_url is required"}
    if not jsonpath_expr:
        return {"error": "jsonpath is required"}

    try:
        data = await read_generated_asset(blob_url)
    except Exception as exc:
        logger.warning("query_asset: could not read blob %s: %s", blob_url, exc)
        return {"error": f"Could not read asset: {exc}"}

    try:
        from jsonpath_ng.ext import parse as _parse
        expr = _parse(jsonpath_expr)
        all_matches = [match.value for match in expr.find(data)]
    except Exception as exc:
        logger.warning("query_asset: JSONPath error for '%s': %s", jsonpath_expr, exc)
        return {"error": f"JSONPath error: {exc}"}

    total = len(all_matches)
    truncated = total > max_results
    return {
        "matches": all_matches[:max_results],
        "total_matches": total,
        "truncated": truncated,
    }


async def write_query_asset(payload: dict[str, Any]) -> dict[str, Any]:
    """Download a blob asset and return a JSONPath-filtered subset.

    Input:
      video_url: str
      job_id: str (required)
      session_id: str (optional)    
      blob_url: str — URL of any generated asset blob (required)
      jsonpath: str — JSONPath expression, e.g. "$.frames[*].timestamp_seconds" (required)
      max_results: int — maximum number of matched values to return (default 50)

    Output:
      result_asset: str — blob URL of the full frames JSON    
      matches: list — matched values (capped at max_results)
      total_matches: int — total number of matches before capping
      truncated: bool — true if total_matches > max_results
    """
    blob_url: str = payload.get("blob_url", "")
    jsonpath_expr: str = payload.get("jsonpath", "")
    max_results: int = max(1, int(payload.get("max_results", _DEFAULT_MAX_RESULTS)))

    if not blob_url:
        return {"error": "blob_url is required"}
    if not jsonpath_expr:
        return {"error": "jsonpath is required"}

    try:
        data = await read_generated_asset(blob_url)
    except Exception as exc:
        logger.warning("query_asset: could not read blob %s: %s", blob_url, exc)
        return {"error": f"Could not read asset: {exc}"}

    try:
        from jsonpath_ng.ext import parse as _parse
        expr = _parse(jsonpath_expr)
        all_matches = [match.value for match in expr.find(data)]
    except Exception as exc:
        logger.warning("query_asset: JSONPath error for '%s': %s", jsonpath_expr, exc)
        return {"error": f"JSONPath error: {exc}"}

    total = len(all_matches)
    truncated = total > max_results

    video_url: str = payload.get("video_url", "")
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None

    if not job_id:
        return {"error": "job_id is required"}

    fps = 1.0
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
            "total_frames": len(all_matches),
            "frames": all_matches,
        },
    )

    return {
        "result_asset": result_asset,
        #"matches": all_matches[:max_results],
        "total_matches": total,
        "truncated": truncated,
    }