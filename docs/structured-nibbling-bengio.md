# Plan: Fix Multi-Video Segment Confusion

## Context

When multiple videos are uploaded, segments detected from all videos are being extracted from the same (wrong) video. The root cause is that `video_url` is not propagated through the analysis pipeline — detection tools return segments without a `video_url` field, the analysis task description doesn't explicitly require preserving it, and `extract_clips_bulk` only accepts a single top-level `video_url` (applied to all segments regardless of source). Even weaker models that follow instructions correctly cannot fix this because the data simply isn't there.

---

## Root Cause Summary

| Layer | Problem |
|---|---|
| `detect_motion.py`, `detect_motion_sports.py`, `detect_objects.py` | Read `frames_asset` (which contains `video_url`) but discard it — segments returned in `summary.segments` have no `video_url` |
| `tasks.py` `make_analysis_task()` | Does not instruct the agent to preserve `video_url` per segment when merging |
| `extract_clips_bulk.py` | Single top-level `video_url` applied to all segments — no per-segment override |
| `tasks.py` `make_processing_task()` | Says to "respect the video_url per segment" but segments_asset contains no such field and the tool has no mechanism for it |

---

## Changes

### 1. `mcp-servers/mcp-server-analysis/app/tools/detect_motion.py`

After `raw = await read_generated_asset(frames_asset)`, read:
```python
video_url: str = raw.get("video_url", "") if isinstance(raw, dict) else ""
```

Add `video_url` to each segment in the `segments` list:
```python
segments = [
    {"start_seconds": seg["start"], "end_seconds": seg["end"], "video_url": video_url}
    for seg in high_motion
]
```

Add `video_url` as top-level field in `full_result`:
```python
full_result = {
    "video_url": video_url,
    "motion_score": ...,
    ...
}
```

Include `video_url` in `summary.segments` entries (same list already updated above).

---

### 2. `mcp-servers/mcp-server-analysis/app/tools/detect_motion_sports.py`

Same pattern as detect_motion:
- Extract `video_url` from the frames_asset blob after reading
- Add `video_url` to each entry in `segments` list
- Add `video_url` to `full_result`

---

### 3. `mcp-servers/mcp-server-analysis/app/tools/detect_objects.py`

Same pattern:
- Extract `video_url` from `raw.get("video_url", "")` after reading frames_asset
- After `segments = aggregate_detections_to_segments(detected_frames)`, add `video_url` to each segment:
  ```python
  for seg in segments:
      seg["video_url"] = video_url
  ```
- Add `video_url` as top-level field in `full_result`

---

### 4. `mcp-servers/mcp-server-processing/app/tools/extract_clips_bulk.py`

Support per-segment `video_url` with top-level `video_url` as fallback:

In the segment loop, replace:
```python
proc = await asyncio.create_subprocess_exec("ffmpeg", "-i", video_url, ...)
```
with:
```python
seg_video_url = seg.get("video_url") or video_url
proc = await asyncio.create_subprocess_exec("ffmpeg", "-i", seg_video_url, ...)
```

Also update the log line:
```python
logger.info("extract_clips_bulk: segment %d/%d from %s → %s (%.2f–%.2f s)",
    i + 1, total_clips, seg_video_url, clip_url, start, end)
```

The top-level `video_url` parameter stays required (backward compat for single-video jobs and as the fallback).

---

### 5. `backend/agent-orchestrator/app/tasks.py` — `make_analysis_task()`

Replace the sentence:
> "Collect the segments from tool summaries across all tool calls and all videos."

With:
> "Collect the segments from tool summaries across all tool calls and all videos. Detection tools automatically include the source `video_url` in each segment — **do NOT strip or omit `video_url` when merging segments**. Every segment passed to `write_segments_asset` must retain its `video_url` field so the processing agent extracts clips from the correct source video."

---

### 6. `backend/agent-orchestrator/app/tasks.py` — `make_processing_task()`

In the "IMPORTANT — extracting clips" section, update the preferred path instruction:
> "Preferred path: call `extract_clips_bulk` once with `segments_asset` (from analysis results), `video_url` (set to the primary/first video URL as a fallback for any segment missing an explicit `video_url`), `job_id`, and `session_id`. For multi-video jobs, segments in `segments_asset` carry a per-segment `video_url` — `extract_clips_bulk` uses the per-segment `video_url` when present and falls back to the top-level `video_url` otherwise."

---

## Critical Files

| File | Change |
|---|---|
| `mcp-servers/mcp-server-analysis/app/tools/detect_motion.py` | Propagate `video_url` from frames_asset into segments and full_result |
| `mcp-servers/mcp-server-analysis/app/tools/detect_motion_sports.py` | Same |
| `mcp-servers/mcp-server-analysis/app/tools/detect_objects.py` | Same |
| `mcp-servers/mcp-server-processing/app/tools/extract_clips_bulk.py` | Per-segment `video_url` with top-level fallback |
| `backend/agent-orchestrator/app/tasks.py` | Strengthen analysis + processing task prompts |

Not changed: `write_segments_asset.py` (already uses `{**seg, ...}` which preserves `video_url` if present — no change needed).

---

## Verification

### Unit tests
```bash
# mcp-server-analysis
cd mcp-servers/mcp-server-analysis
poetry run pytest tests/unit/ -v

# mcp-server-processing
cd mcp-servers/mcp-server-processing
poetry run pytest tests/unit/ -v
```

### Container rebuild (affected services only)
```bash
docker-compose build mcp-server-analysis mcp-server-processing agent-orchestrator
docker-compose up -d mcp-server-analysis mcp-server-processing agent-orchestrator
```

### Manual end-to-end validation
1. Upload two distinct videos (e.g. video A: kitesurfing jumps in the first 30s; video B: calm water)
2. Submit a prompt to extract highlights across both
3. Verify in agent-orchestrator logs that:
   - Each `detect_*` tool call logs segments with `video_url` matching the source video
   - `extract_clips_bulk` logs per-segment source URLs — each segment uses the correct video
4. Confirm the final output video contains clips only from the correct source video per segment
