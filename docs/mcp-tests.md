# MCP Server Unit Tests

## How to Run

Unit tests for both MCP servers run with PyTest inside each service's virtual environment. External dependencies (Blob Storage, FFmpeg, YOLO, Whisper, Anthropic API) are mocked throughout.

### Run all MCP unit tests

```bash
# Analysis server
cd mcp-servers/mcp-server-analysis
poetry run pytest tests/unit/ -v

# Processing server
cd mcp-servers/mcp-server-processing
poetry run pytest tests/unit/ -v
```

### Run a single test file

```bash
poetry run pytest tests/unit/test_router.py -v
poetry run pytest tests/unit/test_extract_frames.py -v
```

### Run by keyword

```bash
poetry run pytest tests/unit/ -k "extract_frames"
poetry run pytest tests/unit/ -k "merge" -v
```

### Coverage report

```bash
poetry run pytest tests/unit/ --cov=app --cov-report=term-missing
```

Coverage requirement: **80% minimum per service**.

---

## Architecture of the Test Suites

```
mcp-servers/
├── mcp-server-analysis/
│   └── tests/unit/
│       ├── test_router.py              # GET /tools, POST /tools/{name}/invoke routing
│       ├── test_extract_frames.py      # Frame filtering from keyframe index
│       ├── test_detect_motion.py       # OpenCV optical flow — motion scoring, segmentation
│       ├── test_detect_motion_sports.py # Sports-tuned optical flow — event detection
│       ├── test_detect_objects.py      # YOLOv8 detection pipeline — mocked inference
│       ├── test_detect_objects_vision.py # Claude vision open-vocab detection — mocked API
│       ├── test_analyze_scene.py       # Claude vision scene analysis — mocked API
│       ├── test_transcribe_audio.py    # Whisper transcription — mocked model
│       ├── test_estimate_height_above_surface.py  # Depth Anything V2 — mocked inference
│       ├── test_read_asset.py          # Blob read — mocked Blob Storage
│       ├── test_write_segments_asset.py # Segment list persistence — mocked Blob Storage
│       ├── test_query_asset.py         # JSONPath filtering of blob assets
│       └── test_write_query_asset.py   # JSONPath filter + blob write for frames bridge
│
└── mcp-server-processing/
    └── tests/unit/
        ├── test_router.py              # GET /tools, POST /tools/{name}/invoke routing
        ├── test_extract_clip.py        # FFmpeg clip extraction — mocked subprocess + blob
        ├── test_extract_clips_bulk.py  # Bulk clip extraction from segments_asset
        ├── test_merge_clips.py         # FFmpeg concatenation — mocked subprocess + blob
        ├── test_split_video.py         # FFmpeg fixed-length splitting — mocked subprocess
        ├── test_transform_video.py     # FFmpeg resize / speed / color grade — mocked subprocess
        ├── test_write_asset.py         # Non-video blob write — mocked Blob Storage
        ├── test_query_asset.py         # JSONPath filtering of blob assets
        └── test_write_query_asset.py   # JSONPath filter + blob write
```

### What is and is not mocked

| Dependency | Mock strategy |
|---|---|
| Azure Blob Storage (upload/download) | `AsyncMock` on `upload_to_blob` / `download_blob` helpers |
| FFmpeg subprocess | `patch("asyncio.create_subprocess_exec")` — `returncode=0`, `communicate` returns `(b"", b"")` |
| YOLOv8 (`ultralytics`) | `patch("app.tools.detect_objects.YOLO")` — returns mock predictions |
| Whisper (`whisper.load_model`) | `patch("app.tools.transcribe_audio.whisper.load_model")` |
| Anthropic API (frontier tools) | `patch("app.tools.model_registry.FrontierModelClient.call_vision_api")` |
| Depth Anything V2 | `patch("app.tools.estimate_height_above_surface.DepthAnythingV2")` |
| PostgreSQL | Not used directly in MCP tools — mocked at the boundary of `extract_frames` (`keyframe_index` passed inline) |

---

## mcp-server-analysis

### `test_router.py`

Tests the FastAPI routing layer shared by all analysis tools.

#### `test_list_tools`

`GET /tools` returns HTTP 200, a JSON list, and includes the core tool names:
`extract_frames`, `detect_motion`, `detect_objects`, `transcribe_audio`.

**Assertions:**
- Status 200
- Response is a list
- `extract_frames`, `detect_motion`, `detect_objects`, `transcribe_audio` present in `{t["name"] for t in tools}`

#### `test_invoke_unknown_tool`

`POST /tools/nonexistent/invoke` returns HTTP 404.

**Assertions:**
- Status 404

#### `test_invoke_extract_frames_sse`

`POST /tools/extract_frames/invoke` with a minimal inline keyframe index returns HTTP 200 with `content-type: text/event-stream` and a body containing `result`.

**What it tests:** The SSE response wrapper around tool invocation — that the router streams an event rather than returning plain JSON.

**Assertions:**
- Status 200
- `content-type` contains `text/event-stream`
- Response body contains `"result"`

---

### `test_extract_frames.py`

Unit tests for `extract_frames` — the prerequisite tool that all detection tools depend on. No network calls; the keyframe index is passed inline.

#### `test_extract_frames_returns_all_when_no_filter`

Input: two-frame keyframe index, no `frame_indices` filter.

**Assertions:**
- `len(result["frames"]) == 2`
- `result["frames"][0]["url"] == "http://frame0.jpg"`

#### `test_extract_frames_filters_by_index`

Input: two-frame keyframe index, `frame_indices: [1]`.

**Assertions:**
- `len(result["frames"]) == 1`
- `result["frames"][0]["url"] == "http://frame1.jpg"`

#### `test_extract_frames_empty_index`

Input: empty keyframe index.

**Assertions:**
- `result["frames"] == []`

---

### `test_detect_motion.py`

Tests `detect_motion` — OpenCV Farneback optical flow motion scoring. FFmpeg and blob calls are mocked; a synthetic list of frame dicts (with real JPEG byte sequences or small synthetic images) is passed as `frames_asset` content.

#### `test_detect_motion_returns_segments`

Input: frames with alternating high/low motion scores (achieved by providing frame images with varying pixel differences in mock return values).

**What it tests:** The segmentation logic — frames exceeding 1.5× the average motion score are grouped into segments.

**Assertions:**
- `result["summary"]["high_motion_segments_count"] >= 1`
- `result["result_asset"]` is a non-empty string (blob URL)
- Each segment in `result["summary"]["segments"]` has `start_seconds` and `end_seconds`

#### `test_detect_motion_all_static_frames`

Input: identical frames (zero optical flow between them).

**What it tests:** The zero-motion edge case — no segments should be returned, and the tool should not raise.

**Assertions:**
- `result["summary"]["high_motion_segments_count"] == 0`
- `result["summary"]["segments"] == []`

#### `test_detect_motion_missing_frames_asset_raises`

Input: payload with no `frames_asset` field.

**Assertions:**
- Raises `KeyError` or `ValueError` (tool validates required inputs)

---

### `test_detect_motion_sports.py`

Tests `detect_motion_sports` — sports-tuned optical flow using absolute pixel-flow magnitude thresholds rather than relative averages.

#### `test_detect_motion_sports_returns_events`

Input: frames simulating a burst of high-intensity motion followed by calm frames.

**What it tests:** Event detection — the sports tool groups frames that exceed `sensitivity × 100` pixel-flow magnitude into discrete events with `type`, `start_seconds`, `end_seconds`.

**Assertions:**
- `result["summary"]["events_count"] >= 1`
- Each entry in `result["summary"]["segments"]` has `start_seconds` < `end_seconds`
- `result["result_asset"]` is set

#### `test_detect_motion_sports_sensitivity_zero`

Input: same burst frames as above, `sensitivity: 0.0`.

**What it tests:** Maximum sensitivity — all frames register as motion events.

**Assertions:**
- `result["summary"]["events_count"] >= 1` (all frames in an event)

#### `test_detect_motion_sports_sensitivity_one`

Input: moderate motion frames, `sensitivity: 1.0`.

**What it tests:** Minimum sensitivity — threshold is so high that no frame qualifies.

**Assertions:**
- `result["summary"]["events_count"] == 0`

---

### `test_detect_objects.py`

Tests `detect_objects` — YOLOv8n COCO-class object detection. `ultralytics.YOLO` is fully mocked.

#### `test_detect_objects_with_detections`

Mock setup: YOLO inference returns a `person` detection with confidence 0.82 on frame 0.

Input: `object_classes: ["person"]`, one-frame `frames_asset`.

**Assertions:**
- `result["summary"]["classes_detected"]` contains `"person"`
- `result["summary"]["total_detections"] == 1`
- `result["result_asset"]` is set

#### `test_detect_objects_no_detections`

Mock setup: YOLO inference returns empty predictions.

Input: `object_classes: ["person"]`.

**What it tests:** The zero-detection path — tool completes without error and returns an empty segment list.

**Assertions:**
- `result["summary"]["total_detections"] == 0`
- `result["summary"]["segments"] == []`

#### `test_detect_objects_missing_object_classes_raises`

Input: `frames_asset` provided, but no `object_classes`.

**Assertions:**
- Raises `KeyError` or `ValueError`

#### `test_detect_objects_invalid_class_ignored`

Mock setup: YOLO inference returns predictions for a class not in the requested list.

Input: `object_classes: ["cat"]`, YOLO returns `person`.

**Assertions:**
- `result["summary"]["classes_detected"]` does not contain `"person"`
- `result["summary"]["total_detections"] == 0`

---

### `test_detect_objects_vision.py`

Tests `detect_objects_vision` — Claude vision open-vocabulary detection. `FrontierModelClient.call_vision_api` is mocked to return structured JSON.

#### `test_detect_objects_vision_with_detections`

Mock setup: Claude API returns `[{"present": true, "confidence": 0.9, "object": "kitesurfer mid-jump", "location_description": "centre frame", "bbox_rough": [0.3, 0.2, 0.6, 0.7]}]` for each frame.

Input: `object_descriptions: ["kitesurfer mid-jump"]`, two-frame `frames_asset`.

**Assertions:**
- `result["summary"]["frames_with_detections"] == 2`
- `result["result_asset"]` is set
- `result["summary"]["model_used"]` is a non-empty string

#### `test_detect_objects_vision_no_detections`

Mock setup: Claude API returns `[{"present": false}]` for all frames.

**Assertions:**
- `result["summary"]["frames_with_detections"] == 0`

#### `test_detect_objects_vision_api_error_does_not_crash`

Mock setup: `call_vision_api` raises `httpx.HTTPError`.

**What it tests:** Per-frame error handling — tool records the error in `result["errors"]` and continues processing remaining frames rather than raising.

**Assertions:**
- Tool returns without raising
- `result["summary"]["frames_with_errors"] >= 1`

#### `test_detect_objects_vision_missing_object_descriptions_raises`

Input: `frames_asset` provided, `object_descriptions` omitted.

**Assertions:**
- Raises `KeyError` or `ValueError`

---

### `test_analyze_scene.py`

Tests `analyze_scene` — Claude vision semantic scene description. `FrontierModelClient.call_vision_api` is mocked.

#### `test_analyze_scene_returns_per_frame_descriptions`

Mock setup: Claude API returns `{"description": "A blue sky with clouds", "objects": ["sky", "clouds"], "activities": [], "setting": "outdoor", "mood": "calm"}` for each frame.

Input: three-frame `frames_asset`.

**Assertions:**
- `result["summary"]["frames_analysed"] == 3`
- `result["summary"]["unique_settings"]` contains `"outdoor"`
- `result["summary"]["common_objects"]` contains `"sky"`
- `result["result_asset"]` is set

#### `test_analyze_scene_with_question`

Input: `question: "Is there any water visible?"`, two-frame `frames_asset`.

Mock setup: Claude returns descriptions acknowledging the question.

**What it tests:** The optional `question` parameter is included in the model prompt.

**Assertions:**
- `result["summary"]["frames_analysed"] == 2`
- Tool does not raise

#### `test_analyze_scene_api_error_does_not_crash`

Mock setup: `call_vision_api` raises on the first batch, succeeds on the second.

**Assertions:**
- Tool returns without raising
- `result["summary"]["frames_with_errors"] >= 1`

---

### `test_transcribe_audio.py`

Tests `transcribe_audio` — Whisper audio transcription. `whisper.load_model` is mocked.

#### `test_transcribe_audio_with_speech`

Mock setup: Whisper model `transcribe()` returns `{"segments": [{"text": "Hello world", "start": 0.0, "end": 2.5}]}`.

Input: `video_url: "http://video.mp4"`.

**What it tests:** Successful transcription — word count, segment count, and `result_asset` blob URL are all populated.

**Assertions:**
- `result["summary"]["segment_count"] == 1`
- `result["summary"]["word_count"] == 2`
- `result["result_asset"]` is set

#### `test_transcribe_audio_empty_transcript`

Mock setup: Whisper returns `{"segments": []}`.

**What it tests:** Empty transcript path — no segments, zero word count, tool completes cleanly.

**Assertions:**
- `result["summary"]["segment_count"] == 0`
- `result["summary"]["word_count"] == 0`
- Tool does not raise

#### `test_transcribe_audio_ffmpeg_error_raises`

Mock setup: FFmpeg audio extraction subprocess returns `returncode=1`.

**Assertions:**
- Raises `RuntimeError` matching `"FFmpeg"` or `"audio extraction"`

---

### `test_estimate_height_above_surface.py`

Tests `estimate_height_above_surface` — Depth Anything V2 Metric Outdoor per-frame depth inference. The `DepthAnythingV2` model class is mocked.

#### `test_estimate_height_returns_events`

Mock setup: Depth Anything returns a depth map where the bottom 20% of the frame (surface sample region) has mean value of 3.0 m for frames 0–2 (airborne) and 0.2 m for frames 3–5 (grounded).

Input: six-frame `frames_asset`, `height_threshold_m: 0.5`.

**Assertions:**
- `result["summary"]["events_count"] >= 1`
- `result["summary"]["peak_height_m"] >= 0.5`
- Each event has `start_seconds`, `end_seconds`, `peak_height_m`
- `result["result_asset"]` is set

#### `test_estimate_height_no_airborne_frames`

Mock setup: Depth Anything returns surface depth of 0.1 m for all frames (camera always on the ground).

Input: `height_threshold_m: 0.5`.

**Assertions:**
- `result["summary"]["events_count"] == 0`
- `result["summary"]["peak_height_m"] < 0.5`

#### `test_estimate_height_tilt_correction_applied`

Mock setup: Depth Anything returns consistent depth values.

Input: `camera_vfov_deg: 94` (GoPro wide-angle), `surface_sample_pct: 0.20`.

**What it tests:** The tilt-correction path is invoked without error. The exact numeric result is not asserted (implementation-specific), but the shape of the output is checked.

**Assertions:**
- `result["result_asset"]` is set
- `result["summary"]` contains `events_count` and `peak_height_m`

#### `test_estimate_height_custom_surface_pct`

Input: `surface_sample_pct: 0.40`.

**What it tests:** Non-default surface sample region is accepted without error.

**Assertions:**
- Tool returns without raising

---

### `test_read_asset.py`

Tests `read_asset` — reads any non-video session asset from Blob Storage. Blob download is mocked.

#### `test_read_asset_json`

Mock setup: blob download returns `b'{"key": "value"}'` with `content_type: application/json`.

**Assertions:**
- `result["content"] == '{"key": "value"}'`
- `result["content_type"] == "application/json"`
- `result["size_bytes"] == 17`

#### `test_read_asset_respects_max_bytes`

Mock setup: blob download returns 2000 bytes of data.

Input: `max_bytes: 100`.

**Assertions:**
- `len(result["content"]) <= 100`

#### `test_read_asset_missing_blob_url_raises`

Input: payload with no `blob_url` field.

**Assertions:**
- Raises `KeyError` or `ValueError`

---

### `test_write_segments_asset.py`

Tests `write_segments_asset` — persists the final merged segment list to blob. Blob upload is mocked.

#### `test_write_segments_asset_success`

Mock setup: `upload_to_blob` returns `"http://blob/segments.json"`.

Input: `segments: [{"start_seconds": 1.0, "end_seconds": 3.5}]`.

**Assertions:**
- `result["segments_asset"] == "http://blob/segments.json"`
- `result["segments_count"] == 1`

#### `test_write_segments_asset_multiple_segments`

Input: three segments.

**Assertions:**
- `result["segments_count"] == 3`

#### `test_write_segments_asset_empty_segments_raises`

Input: `segments: []`.

**Assertions:**
- Raises `ValueError` (empty segment lists are invalid per the input schema `minItems: 1`)

#### `test_write_segments_asset_invalid_segment_raises`

Input: `segments: [{"start_seconds": 5.0, "end_seconds": 3.0}]` (end before start).

**Assertions:**
- Raises `ValueError` (end must be strictly greater than start)

---

### `test_query_asset.py`

Tests `query_asset` — applies a JSONPath expression to a blob asset. Blob download is mocked.

#### `test_query_asset_extracts_matches`

Mock setup: blob contains `{"frames": [{"timestamp_seconds": 0.0}, {"timestamp_seconds": 1.0}]}`.

Input: `jsonpath: "$.frames[*].timestamp_seconds"`.

**Assertions:**
- `result["matches"] == [0.0, 1.0]`
- `result["total_matches"] == 2`
- `result["truncated"] == False`

#### `test_query_asset_truncates_at_max_results`

Mock setup: blob contains 100 items.

Input: `jsonpath: "$[*]"`, `max_results: 10`.

**Assertions:**
- `len(result["matches"]) == 10`
- `result["truncated"] == True`

#### `test_query_asset_no_matches`

Input: `jsonpath: "$.nonexistent[*]"`.

**Assertions:**
- `result["matches"] == []`
- `result["total_matches"] == 0`

---

### `test_write_query_asset.py`

Tests `write_query_asset` — JSONPath filter + blob write; bridges free detection tools with frontier vision tools.

#### `test_write_query_asset_filters_and_writes`

Mock setup: blob contains motion result with 5 frames; 3 are in-segment (`segment_index >= 0`). `upload_to_blob` returns a new blob URL.

Input: `jsonpath: "$.frames[?(@.segment_index >= 0)]"`.

**Assertions:**
- `result["total_matches"] == 3`
- `result["result_asset"]` is a non-empty string
- `len(result["matches"]) == 3`

#### `test_write_query_asset_empty_filter`

Input: `jsonpath: "$.frames[?(@.motion_score > 999)]"` (no frames exceed this threshold).

**Assertions:**
- `result["total_matches"] == 0`
- `result["result_asset"]` is still set (empty frames blob is valid)

---

## mcp-server-processing

### `test_router.py`

Tests the FastAPI routing layer shared by all processing tools.

#### `test_list_tools`

`GET /tools` returns HTTP 200 and includes all registered processing tools.

**Assertions:**
- Status 200
- `split_video`, `extract_clip`, `extract_clips_bulk`, `merge_clips`, `transform_video`, `write_asset` present in `{t["name"] for t in tools}`

#### `test_invoke_unknown_tool`

`POST /tools/nonexistent/invoke` returns HTTP 404.

**Assertions:**
- Status 404

---

### `test_extract_clip.py`

Tests `extract_clip` — FFmpeg time-bounded clip extraction. `asyncio.create_subprocess_exec` and `upload_to_blob` are mocked.

#### `test_extract_clip_success`

Mock setup: FFmpeg subprocess `returncode=0`. `upload_to_blob` returns `"http://blob/clip.mp4"`.

Input: `video_url`, `start_seconds: 10.0`, `end_seconds: 25.0`.

**Assertions:**
- `result["clip_url"] == "http://blob/clip.mp4"`

#### `test_extract_clip_raises_on_ffmpeg_error`

Mock setup: FFmpeg subprocess `returncode=1`, stderr `b"FFmpeg error"`.

**Assertions:**
- Raises `RuntimeError` matching `"FFmpeg extract_clip failed"`

#### `test_extract_clip_appends_to_clip_list`

Mock setup: `upload_to_blob` returns a clip URL; `download_blob` returns an existing `clip_list.json` with one entry. `upload_to_blob` for the updated list returns a new URL.

Input: `clip_list_asset: "http://blob/clip_list.json"` (existing list).

**What it tests:** The clip-list chaining pattern — each `extract_clip` call appends to an existing list rather than replacing it.

**Assertions:**
- `result["clips_collected"] == 2`
- `result["clip_list_asset"]` is set

#### `test_extract_clip_end_before_start_raises`

Input: `start_seconds: 30.0`, `end_seconds: 10.0`.

**Assertions:**
- Raises `ValueError` (end must be after start)

---

### `test_extract_clips_bulk.py`

Tests `extract_clips_bulk` — bulk extraction of all segments from a `segments_asset` blob in one tool call.

#### `test_extract_clips_bulk_from_segments_asset`

Mock setup: `download_blob` returns a `segments.json` with two segments. FFmpeg succeeds for each. `upload_to_blob` returns distinct clip URLs.

Input: `segments_asset: "http://blob/segments.json"`.

**Assertions:**
- `result["clips_extracted"] == 2`
- `len(result["clip_urls"]) == 2`
- `result["clip_list_asset"]` is set

#### `test_extract_clips_bulk_from_inline_segments`

Input: `segments: [{"start_seconds": 0.0, "end_seconds": 5.0}, {"start_seconds": 10.0, "end_seconds": 15.0}]` (no `segments_asset`).

**What it tests:** The inline fallback path when `segments_asset` is not available.

**Assertions:**
- `result["clips_extracted"] == 2`

#### `test_extract_clips_bulk_empty_segments_returns_empty`

Input: `segments: []` (inline empty list).

**Assertions:**
- `result["clips_extracted"] == 0`
- `result["clip_urls"] == []`

#### `test_extract_clips_bulk_ffmpeg_error_raises`

Mock setup: FFmpeg returns `returncode=1` on the second clip.

**Assertions:**
- Raises `RuntimeError`

---

### `test_merge_clips.py`

Tests `merge_clips` — FFmpeg concat filter to join clips into a single output. `asyncio.create_subprocess_exec` and `upload_to_blob` are mocked.

#### `test_merge_clips_success`

Mock setup: FFmpeg `returncode=0`. `upload_to_blob` returns `"http://blob/output.mp4"`.

Input: `clip_urls: ["http://clip1.mp4", "http://clip2.mp4"]`.

**Assertions:**
- `result["output_url"] == "http://blob/output.mp4"`

#### `test_merge_clips_empty_raises`

Input: `clip_urls: []`.

**Assertions:**
- Raises `ValueError` matching `"clip_urls must not be empty"`

#### `test_merge_clips_from_clip_list_asset`

Mock setup: `download_blob` returns `clip_list.json` containing two clip URLs. FFmpeg succeeds. `upload_to_blob` returns an output URL.

Input: `clip_list_asset: "http://blob/clip_list.json"` (no inline `clip_urls`).

**What it tests:** The preferred `clip_list_asset` path — `merge_clips` reads the list from blob rather than requiring the caller to inline all URLs.

**Assertions:**
- `result["output_url"]` is set

#### `test_merge_clips_ffmpeg_error_raises`

Mock setup: FFmpeg `returncode=1`.

**Assertions:**
- Raises `RuntimeError`

---

### `test_split_video.py`

Tests `split_video` — FFmpeg fixed-length segment splitting.

#### `test_split_video_success`

Mock setup: FFmpeg `returncode=0`. `upload_to_blob` is called once per segment and returns distinct URLs.

Input: `video_url`, `segment_length_seconds: 30`.

**Assertions:**
- `result["segment_urls"]` is a non-empty list
- Each URL is a string

#### `test_split_video_ffmpeg_error_raises`

Mock setup: FFmpeg `returncode=1`.

**Assertions:**
- Raises `RuntimeError`

---

### `test_transform_video.py`

Tests `transform_video` — FFmpeg resize, speed change, and color grade operations.

#### `test_transform_video_speed`

Mock setup: FFmpeg `returncode=0`. `upload_to_blob` returns an output URL.

Input: `operations: [{"type": "speed", "params": {"factor": 2.0}}]`.

**Assertions:**
- `result["output_url"]` is set
- FFmpeg was called with a `setpts` filter argument

#### `test_transform_video_resize`

Input: `operations: [{"type": "resize", "params": {"width": 1280, "height": 720}}]`.

**Assertions:**
- `result["output_url"]` is set
- FFmpeg was called with a `scale` filter argument

#### `test_transform_video_color_grade`

Input: `operations: [{"type": "color_grade", "params": {"brightness": 0.1, "contrast": 1.2}}]`.

**Assertions:**
- `result["output_url"]` is set
- FFmpeg was called with an `eq` filter argument

#### `test_transform_video_multiple_operations`

Input: `operations: [{"type": "speed", "params": {"factor": 0.5}}, {"type": "resize", "params": {"width": 640, "height": 360}}]`.

**What it tests:** Operations are applied in order and composed into a single FFmpeg filter chain.

**Assertions:**
- `result["output_url"]` is set
- FFmpeg invoked once (not once per operation)

#### `test_transform_video_invalid_operation_raises`

Input: `operations: [{"type": "unknown_op"}]`.

**Assertions:**
- Raises `ValueError`

#### `test_transform_video_ffmpeg_error_raises`

Mock setup: FFmpeg `returncode=1`.

**Assertions:**
- Raises `RuntimeError`

---

### `test_write_asset.py`

Tests `write_asset` — persists generated non-video content (JSON, text, CSV) to Blob Storage.

#### `test_write_asset_json_success`

Mock setup: `upload_to_blob` returns `"http://blob/analysis.json"`.

Input: `content: '{"result": "ok"}'`, `filename: "analysis.json"`, `content_type: "application/json"`.

**Assertions:**
- `result["blob_url"] == "http://blob/analysis.json"`
- `result["filename"] == "analysis.json"`
- `result["size_bytes"] > 0`

#### `test_write_asset_text_success`

Input: `content: "plain text"`, `filename: "notes.txt"`, `content_type: "text/plain"`.

**Assertions:**
- `result["blob_url"]` is set

#### `test_write_asset_missing_content_raises`

Input: payload with no `content` field.

**Assertions:**
- Raises `KeyError` or `ValueError`

#### `test_write_asset_missing_filename_raises`

Input: `content` provided, no `filename`.

**Assertions:**
- Raises `KeyError` or `ValueError`

---

### `test_query_asset.py`

Tests `query_asset` — JSONPath filtering of any blob asset. Same contract as the analysis server's `query_asset`.

#### `test_query_asset_extracts_clip_urls`

Mock setup: blob contains `{"clip_urls": ["http://clip1.mp4", "http://clip2.mp4"]}`.

Input: `jsonpath: "$.clip_urls[*]"`.

**Assertions:**
- `result["matches"] == ["http://clip1.mp4", "http://clip2.mp4"]`
- `result["total_matches"] == 2`

#### `test_query_asset_truncates_at_max_results`

Mock setup: blob contains 100 entries.

Input: `max_results: 5`.

**Assertions:**
- `len(result["matches"]) == 5`
- `result["truncated"] == True`

#### `test_query_asset_no_matches`

Input: `jsonpath: "$.nonexistent[*]"`.

**Assertions:**
- `result["matches"] == []`

---

### `test_write_query_asset.py`

Tests `write_query_asset` on the processing server — same JSONPath-filter-then-write pattern, primarily used to pass filtered detection results to frontier tools.

#### `test_write_query_asset_filters_and_writes`

Mock setup: blob contains detection result with 4 frames; 2 have `detection_count > 0`.

Input: `jsonpath: "$.frames[?(@.detection_count > 0)]"`.

**Assertions:**
- `result["total_matches"] == 2`
- `result["result_asset"]` is set

#### `test_write_query_asset_empty_result_is_valid`

Input: `jsonpath: "$.frames[?(@.detection_count > 999)]"` (no matches).

**Assertions:**
- `result["total_matches"] == 0`
- `result["result_asset"]` is set (empty blob is valid)

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImportError: No module named 'ultralytics'` | YOLOv8 not installed in the venv | `poetry install` in `mcp-server-analysis/` |
| `ImportError: No module named 'whisper'` | Whisper not installed | `poetry install` |
| `ModuleNotFoundError: app.tools.xxx` | Test run from wrong directory | `cd mcp-servers/mcp-server-analysis` before running pytest |
| Tests pass individually but fail together | Module-level import side effects (e.g. YOLO model load on import) | Ensure model load is inside the function, not at module scope |
| `RuntimeError: Event loop is closed` | `pytest-asyncio` mode not set | Add `asyncio_mode = "auto"` to `pyproject.toml` `[tool.pytest.ini_options]` |
| Blob mock not applied | Patch path targets the wrong module | Patch where the symbol is **used** (`app.tools.detect_motion.upload_to_blob`), not where it's defined |
| FFmpeg mock not intercepting subprocess | Multiple code paths to subprocess creation | Check that the patch matches `asyncio.create_subprocess_exec` in the exact module that calls it |
