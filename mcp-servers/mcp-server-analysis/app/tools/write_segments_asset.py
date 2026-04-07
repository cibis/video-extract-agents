"""write_segments_asset tool — persist a merged segments list to blob storage."""
import logging
import uuid
from typing import Any

from app.tools.generated_asset_store import write_generated_asset

logger = logging.getLogger(__name__)


async def write_segments_asset(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Write a merged segments list to blob storage.

    Call this at the end of the analysis task after collecting and merging
    all segments from detection/motion tool summaries across all tool calls.

    Input:
      segments: list[{start_seconds, end_seconds, ...}]  (required)
      job_id: str (required)
      session_id: str (optional)

    Output:
      segments_asset: str — blob URL of the merged segments JSON
                            pass as segments_asset to extract_clips_bulk
      segments_count: int
    """
    segments: list = payload.get("segments", [])
    job_id: str = payload.get("job_id", "")
    session_id: str | None = payload.get("session_id") or None

    if not segments:
        raise ValueError("segments list is empty — nothing to persist")

    # Normalise segments: if a model writes start == end (point-in-time instead of
    # a range), expand to a 3-second window centred on that timestamp so
    # extract_clips_bulk receives valid non-zero-duration clips.
    _DEFAULT_PAD = 1.5  # seconds either side
    normalised: list = []
    for seg in segments:
        s = max(0.0, float(seg["start_seconds"]))
        e = float(seg["end_seconds"])
        if e <= s:
            logger.warning(
                "write_segments_asset: segment end <= start (%.3f <= %.3f) — "
                "expanding by ±%.1f s",
                e, s, _DEFAULT_PAD,
            )
            s = max(0.0, s - _DEFAULT_PAD)
            e = float(seg["start_seconds"]) + _DEFAULT_PAD
        normalised.append({**seg, "start_seconds": s, "end_seconds": e})

    filename = f"merged_segments_{uuid.uuid4().hex[:8]}.json"
    segments_asset = await write_generated_asset(
        session_id=session_id,
        job_id=job_id,
        data_type="segments",
        filename=filename,
        data=normalised,
    )

    logger.info(
        "write_segments_asset: wrote %d segments to %s",
        len(normalised), segments_asset,
    )
    return {
        "segments_asset": segments_asset,
        "segments_count": len(normalised),
    }
