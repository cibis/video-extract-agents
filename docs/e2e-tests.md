# End-to-End Pipeline Tests

## How to Run

### The correct way — all e2e tests on this machine

All tests run **inside Docker** (no host-side Python, FFmpeg, or pytest needed).
The script requires bash. Three ways to run it on Windows:

**Option 1 — Git Bash terminal** (recommended)
Open Git Bash (Start menu → "Git Bash"), navigate to the project root, then run:

**Option 2 — VS Code integrated terminal**
Open a new terminal in VS Code, click the shell dropdown (next to `+`) and select **Git Bash**, then run:

**Option 3 — From CMD, invoke Git Bash directly**
```
"C:\Program Files\Git\bin\bash.exe" scripts/run-e2e-local.sh
```

For options 1 and 2, run from the project root:

```bash
bash scripts/run-e2e-local.sh
```

The script:
1. Builds and starts the full Docker Compose stack (`docker-compose up -d --build`)
2. Waits for `api-gateway` to become healthy
3. Runs `pytest tests/e2e/` inside a `test-runner` container

Pass extra pytest arguments after the script name:

```bash
# Run a single test by keyword
bash scripts/run-e2e-local.sh -k test_detect_motion

# Run only the follow-up job tests
bash scripts/run-e2e-local.sh tests/e2e/test_followup_job.py

# Include frontier tests (requires API key)
ANTHROPIC_API_KEY=sk-ant-... bash scripts/run-e2e-local.sh
```

### Run only local-model tests (no API key required)

```bash
bash scripts/run-e2e-local.sh \
  --ignore=tests/e2e/test_detect_objects_vision.py \
  --ignore=tests/e2e/test_analyze_scene.py
```

### Run frontier tests (requires Anthropic API key)

```bash
ANTHROPIC_API_KEY=sk-ant-... bash scripts/run-e2e-local.sh
```

Frontier tests (`test_detect_objects_vision.py`, `test_analyze_scene.py`) are automatically **skipped** when `ANTHROPIC_API_KEY` is not set — they will not fail the suite.

---

## Architecture of the Test Suite

```
tests/e2e/
├── conftest.py               # Pytest fixtures: service URLs, HTTP client, auth headers
├── video_factory.py          # FFmpeg synthetic video generators (no binary files in git)
├── helpers.py                # Shared: upload_video, wait_for_indexed, wait_for_job, assertions
├── test_detect_motion.py
├── test_detect_motion_sports.py
├── test_detect_objects.py
├── test_detect_objects_vision.py       # Frontier — skipped without API key
├── test_analyze_scene.py               # Frontier — skipped without API key
├── test_transcribe_audio.py
├── test_estimate_height_above_surface.py
└── test_followup_job.py                # Multi-job / session continuity scenarios
```

### No binary files committed

All test videos are generated at runtime using FFmpeg's built-in `lavfi` virtual input sources. Nothing is committed to source control. Videos are created in pytest's `tmp_path` directory and discarded when the test process exits.

| Factory function | FFmpeg source | Size | Purpose |
|---|---|---|---|
| `make_motion_video` | `testsrc` | ~60 KB | Moving test card; strong optical flow |
| `make_sports_video` | `testsrc2` | ~70 KB | High-contrast bursts; sports motion events |
| `make_static_video` | `color=c=blue` | ~20 KB | Near-zero motion; vision model baseline |
| `make_object_video` | `testsrc` | ~60 KB | Abstract pattern; YOLO pipeline validation |
| `make_audio_video` | `color=black` + `sine=f=440` | ~30 KB | Continuous audio; Whisper transcription |
| `make_pov_video` | `testsrc` | ~30 KB | Stand-in POV footage; Depth Anything V2 height estimation |

### Shared test flow

Every detection-type test follows the same six-step pattern:

```
1. generate synthetic video (FFmpeg lavfi, ~1 second to create)
2. POST /v1/sessions               → session_id
3. upload_video()                  → video_id
   POST /v1/videos {sessionId}     → {videoId, uploadUrl}
   PUT  uploadUrl with video bytes
4. wait_for_indexed()              → polls session assets until preprocessing completes
5. POST /v1/jobs {videoId, sessionId, prompt}  → job_id
6. wait_for_job()                  → polls every 4s until completed/failed
7. assert_job_succeeded()          → accepts output_url OR no_matching_segments
8. assert_tool_invoked()           → checks job_logs for the expected tool name
```

---

## Test File Reference

---

### `test_detect_motion.py`

**Tool under test:** `detect_motion` (OpenCV optical flow, local, free)

**Video:** `make_motion_video` — 8-second FFmpeg test card (`testsrc`) at 320×180, 5 fps. Moving edges and colour bars generate consistent optical flow across all frames.

**Prompt:** `"Extract all segments with significant movement and motion"`

**What the pipeline does:**
1. Planner agent reads the prompt and the tool catalogue. Motion-specific language and the `detect_motion` tool's `capability_tags` steer the planner to select it.
2. Analysis agent calls `extract_frames` → receives 129 keyframe URLs from the pre-computed index.
3. Analysis agent calls `detect_motion` with the frames asset. OpenCV `calcOpticalFlowFarneback` computes per-frame motion scores; segments exceeding 1.5× the average score are identified.
4. Analysis agent calls `write_segments_asset` → persists segment list to blob.
5. Processing agent calls `extract_clips_bulk` with the segments asset → FFmpeg extracts each clip.
6. Processing agent calls `merge_clips` → FFmpeg concatenates clips into a final MP4.
7. Orchestrator writes `output_url` to the `jobs` table and creates `outputs` + `session_assets` rows.

**Acceptance criteria:**
- Job status is `completed`.
- `output_url` is set (motion video almost always produces detectable segments), OR result contains `no_matching_segments` (valid if optical flow scores are uniformly low).
- `detect_motion` appears in job logs.

---

### `test_detect_motion_sports.py`

**Tool under test:** `detect_motion_sports` (OpenCV optical flow, sports-tuned, local, free)

**Video:** `make_sports_video` — 8-second FFmpeg `testsrc2` at 320×180, 5 fps. `testsrc2` produces higher-contrast, faster-changing patterns than `testsrc`, generating stronger motion bursts.

**Prompt:** `"Extract all sports action moments, jumps, and high-intensity tricks"`

**What the pipeline does:**
Same as `detect_motion` but the planner selects `detect_motion_sports` because of the sports/jumps/tricks language in the prompt matching the tool's `specialization: sports` tag. The underlying algorithm uses a pixel-flow magnitude threshold (sensitivity × 100) rather than a relative average, making it more sensitive to sudden high-intensity bursts typical of jump events.

**Acceptance criteria:**
- Job status is `completed`.
- `detect_motion_sports` appears in job logs.

---

### `test_detect_objects.py`

**Tool under test:** `detect_objects` (YOLOv8n, local, free)

**Video:** `make_object_video` — 8-second FFmpeg test card (`testsrc`) at 320×180, 5 fps.

**Prompt:** `"Extract all segments containing a person"`

**What the pipeline does:**
1. Planner selects `detect_objects` because "person" is a standard COCO class and the tool's description explicitly lists COCO-class detection.
2. Analysis agent calls `extract_frames` then `detect_objects` with `object_classes: ["person"]`. YOLOv8n runs inference on each frame batch in a thread pool executor.
3. On a synthetic test-card video, YOLO will almost certainly return 0 detections. The analysis agent writes an empty segments list and sets `segments_count: 0`.
4. Processing agent reads `segments_count: 0` and returns `{"output_url": null, "reason": "no_matching_segments"}`.
5. Job is marked `completed` with a null output_url and the reason string in the result field.

**Acceptance criteria:**
- Job status is `completed` (not `failed` — a crash-free run is what matters here).
- Result is either a non-null `output_url` OR contains `no_matching_segments`. Both are valid.
- `detect_objects` appears in job logs.

**Note:** The purpose of this test is to verify that the YOLO pipeline runs end-to-end without errors. Detection accuracy on synthetic video is not tested. To test actual YOLO accuracy, a real video with COCO-class objects would be required.

---

### `test_detect_objects_vision.py`

**Tool under test:** `detect_objects_vision` (Claude vision via LiteLLM, frontier, API cost)

**Requires:** `ANTHROPIC_API_KEY` set in environment. Skipped otherwise.

**Video:** `make_static_video` — 8-second solid blue field at 320×180, 2 fps. Minimal visual content keeps API token cost low (blue frames are simple to encode and describe).

**Prompt:** `"Extract segments containing colourful geometric patterns or abstract shapes — use vision-based detection"`

**What the pipeline does:**
1. Planner selects `detect_objects_vision` because the target description ("colourful geometric patterns", "abstract shapes") is not a COCO class and the prompt explicitly asks for vision-based detection. The tool's `capability_tags` include `open-vocabulary` and its cost tier is `frontier`.
2. Analysis agent calls `extract_frames` then `detect_objects_vision` with `object_descriptions: ["colourful geometric patterns", "abstract shapes"]`. `FrontierModelClient` batches frames and calls the Claude vision API for each batch.
3. Claude returns per-frame detection results as a JSON array. Results are assembled into segments.
4. Processing agent extracts and merges clips (or returns `no_matching_segments` if Claude found nothing in the solid blue frames).
5. Job completes.

**Acceptance criteria:**
- Job status is `completed`.
- `detect_objects_vision` appears in job logs.

---

### `test_analyze_scene.py`

**Tool under test:** `analyze_scene` (Claude vision via LiteLLM, frontier, API cost)

**Requires:** `ANTHROPIC_API_KEY` set in environment. Skipped otherwise.

**Video:** `make_motion_video` — 8-second animated test card at 320×180, 5 fps. The moving pattern provides visual variation across frames, making scene descriptions more meaningful.

**Prompt:** `"Describe and extract the key scenes from this video. Analyse what is happening in each scene and compile the most interesting moments."`

**What the pipeline does:**
1. Planner selects `analyze_scene` because the prompt asks for description and scene understanding — not detection of specific objects or motion events. The tool's description matches: "semantic scene understanding".
2. Analysis agent calls `extract_frames` then `analyze_scene`. `FrontierModelClient` sends each frame batch to Claude with a structured prompt requesting description, objects, activities, setting, and mood per frame.
3. Claude returns structured JSON scene descriptions. Top objects are aggregated by frequency across frames.
4. Scene metadata is used to build a segment list. Processing agent extracts clips and merges them.
5. Job completes with output_url or no_matching_segments.

**Acceptance criteria:**
- Job status is `completed`.
- `analyze_scene` appears in job logs.
- Allowed extra timeout: 300 seconds (multiple Claude API calls per batch).

---

### `test_transcribe_audio.py`

**Tool under test:** `transcribe_audio` (OpenAI Whisper, local, free)

**Video:** `make_audio_video` — 10-second black video with a continuous 440 Hz sine tone audio track at 160×90, 1 fps, AAC 32k.

**Prompt:** `"Extract segments based on speech and audio content in this video"`

**What the pipeline does:**
1. Planner selects `transcribe_audio` because the prompt mentions speech and audio content. This tool has `capability_tags` that include `speech` and `audio`.
2. Analysis agent calls `transcribe_audio` with the video URL. FFmpeg extracts audio (`-vn -ar 16000 -ac 1`); Whisper "base" model transcribes it.
3. A 440 Hz sine tone produces a sparse or empty transcript (Whisper may output silence or a short noise token). `segment_count: 0` is the expected outcome.
4. Processing agent receives empty segments and returns `no_matching_segments`.
5. Job completes with `output_url: null`.

**Acceptance criteria:**
- Job status is `completed`.
- `transcribe_audio` appears in job logs.
- Allowed extra timeout: 240 seconds (Whisper model load can take ~30 seconds on first invocation).

**Note:** The test verifies the audio pipeline runs without errors. Transcript accuracy is not tested. To test actual transcription, a video with real spoken content would be needed.

---

### `test_estimate_height_above_surface.py`

**Tool under test:** `estimate_height_above_surface` (Depth Anything V2 Metric Outdoor Small, local, free)

**Video:** `make_pov_video` — 6-second FFmpeg test card (`testsrc`) at 320×180, 2 fps. Depth Anything V2 Metric runs on any image and returns absolute depth values in metres regardless of scene content, so visual realism is not required.

**Tests:**

#### `test_estimate_height_above_surface_pipeline`

**Prompt:** `"Estimate the camera height above the ground surface in this first-person POV footage and identify any moments where the camera is elevated above the surface"`

**What the pipeline does:**
1. Planner selects `estimate_height_above_surface` because the prompt contains height/POV/surface language matching the tool's `capability_tags: [height, pov, surface, depth]` and `specialization: sports`. The planner also selects `extract_frames` as the prerequisite step (the tool takes `frames_asset`, not `keyframe_index_asset`).
2. Analysis agent calls `extract_frames` → receives frame URLs from the pre-computed keyframe index.
3. Analysis agent calls `estimate_height_above_surface` with `frames_asset = <result_asset from extract_frames>`. Depth Anything V2 Metric runs per-frame in a thread pool executor; for each frame the bottom 20% of the depth map is sampled as the surface distance (= camera height in metres).
4. Frames above `height_threshold_m` (default 0.5 m) are grouped into airborne events. Results are written to a blob as `{events, segments, frames, peak_height_m}`.
5. Analysis agent calls `write_segments_asset` with any detected segments.
6. Processing agent extracts clips and merges them (or returns `no_matching_segments` if no frames exceed the threshold).
7. Job completes.

**Acceptance criteria:**
- Job status is `completed`.
- `estimate_height_above_surface` appears in job logs (`extract_frames` is implicitly called first but not separately asserted — it cannot be skipped since the tool requires `frames_asset`).

#### `test_estimate_height_above_surface_analysis_asset_registered`

**What it tests:** After a successful height estimation job, the orchestrator registers the result blob as a `job_analysis_result` session asset with a description mentioning "height" (generated by `_describe_analysis_asset()` in `crew.py`).

**Acceptance criteria:**
- Job status is `completed`.
- `estimate_height_above_surface` appears in job logs.
- `GET /v1/sessions/{id}/assets` contains at least one `job_analysis_result` entry with `"height"` in the description field.

**Note:** Depth Anything V2 runs on CPU per frame and is slower than OpenCV tools. The default `frame_batch_size` is 20; for the short synthetic test video (≤ 12 frames at 2 fps) this means a single batch.

---

### `test_followup_job.py`

Contains four independent scenarios testing session continuity and `parentJobId` propagation.

---

#### Scenario 1: Extract → Speed-up transform

**What it tests:** Job 2 can reference Job 1's output via `parentJobId` and apply a transformation.

**Steps:**
1. Upload a motion video to a new session.
2. Job 1: `"Extract all segments with significant movement"` → prompt targets `detect_motion` → produces merged output video.
3. Job 2: `"Speed up the output from the previous job by 2x"` with `parentJobId = job1_id`. The orchestrator reads session context, finds Job 1's `job_output_video` asset, and calls `transform_video` with `{type: "speed", factor: 2}`.
4. Assert Job 2 output_url differs from Job 1 output_url.
5. Assert session assets contain ≥ 2 `job_output_video` entries.

**Why it matters:** Verifies the end-to-end `parentJobId` path: API Gateway → Service Bus `JOB_QUEUED` payload → Agent Orchestrator `run_crew()` → `crew.py` parent job context loading → processing agent `transform_video` call.

---

#### Scenario 2: Multi-video session

**What it tests:** Multiple videos in a single session; Job 2 merges results from Job 1 across different source videos.

**Steps:**
1. Upload two different videos (motion + sports) to the same session.
2. Job 1: extracts motion clips from video 1.
3. Job 2: extracts sports clips from video 2 with `parentJobId = job1_id`, prompting the orchestrator to merge both jobs' clips.
4. Assert both jobs complete successfully.
5. Assert session has ≥ 2 output assets.

**Why it matters:** Verifies `session_id` binds multiple videos and jobs together; the orchestrator's `tasks.py` correctly loads parent job context and session assets from multiple source videos.

---

#### Scenario 3: Re-transform (slow motion)

**What it tests:** A chain of three artefacts — original video → extracted clips → slow-motion version.

**Steps:**
1. Upload motion video.
2. Job 1: extract motion segments → output_url set.
3. Job 2: `"Transform the previous output to slow motion at 0.5x speed"` with `parentJobId = job1_id`. Orchestrator calls `transform_video` with `{type: "speed", factor: 0.5}` on Job 1's output.
4. Assert two distinct output artefacts exist in the session.

**Why it matters:** Verifies the orchestrator correctly resolves `parent_job_id → session_assets → blob_url` for the `transform_video` tool, producing a new independent output file.

---

#### Scenario 4: Job history assets (analysis results registered and reusable)

**What it tests:** After Job 1 completes, all analysis tool result blobs (motion detection JSON, merged segments JSON) are registered in `session_assets` as `job_analysis_result` entries with descriptions. Job 2 reads this enriched history and completes successfully.

**Steps:**
1. Upload a motion video to a new session.
2. Job 1: `"Extract all segments with significant movement and motion"` → planner selects `detect_motion` → optical flow finds motion segments → merged segments JSON and motion detection JSON are registered in `session_assets` as `job_analysis_result` entries → output video produced.
3. Assert `GET /v1/sessions/{id}/assets` contains at least one entry with `asset_type == "job_analysis_result"` and `source_job_id == job1_id`.
4. Assert each `job_analysis_result` asset has a non-empty `description` (e.g. `"Motion detection (optical flow) — 6 high-motion segments"`).
5. Assert the `job_output_video` asset for Job 1 has a non-empty `description` (includes the job prompt).
6. Job 2: `"Speed up the output from the previous job by 2x"` with `parentJobId = job1_id` → completes successfully.
7. Assert Job 2 produces a distinct output URL.
8. Assert session has ≥ 2 `job_output_video` assets.

**Why it matters:** Verifies the enriched job history pipeline end-to-end: analysis results are persisted to `session_assets` in `crew.py` after kickoff, `get_job_asset_manifest()` fetches them via `source_job_id`, `_format_job_history()` renders them with clear labels, and the follow-up job planner can locate and reuse prior analysis files without re-running expensive tools.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `wait_for_indexed` timeout | Preprocessing worker not running or crashed | `docker logs docker-compose-preprocessing-worker-1` |
| `wait_for_job` timeout | Agent orchestrator or MCP server crashed | `docker logs docker-compose-agent-orchestrator-1` |
| Job status `failed`, error mentions model | Invalid LiteLLM model string in `.env` | Check `LITELLM_MODEL` in `infrastructure/docker-compose/.env` — must be `anthropic/claude-sonnet-4-6` |
| Frontier tests skipped | `ANTHROPIC_API_KEY` not set | `export ANTHROPIC_API_KEY=sk-ant-...` |
| FFmpeg not found | FFmpeg not installed on host | `winget install ffmpeg` or `choco install ffmpeg` |
| `upload_video` PUT returns 404 | Blob-proxy route not registered | Ensure api-gateway container is rebuilt and running |
| Job fails immediately | Video not indexed before job creation | Increase `wait_for_indexed` timeout or check preprocessing worker |
