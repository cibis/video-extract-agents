# AI Containers — Deep Dive

This document explains how each AI container works, where it sits in the job processing sequence, and exactly what data it receives and produces. It covers the four containers that perform AI reasoning or model-driven video processing: the **preprocessing-worker** (model-free, but feeds every AI container), the **agent-orchestrator** and its three internal agents, **mcp-server-analysis**, and **mcp-server-processing**.

---

## Table of Contents

- [1. Job Processing Sequence at a Glance](#1-job-processing-sequence-at-a-glance)
- [2. Preprocessing Worker — Keyframe Extraction](#2-preprocessing-worker-keyframe-extraction)
  - [Position in sequence](#position-in-sequence)
  - [What it receives](#what-it-receives)
  - [What it does](#what-it-does)
  - [What it produces](#what-it-produces)
- [3. Agent Orchestrator](#3-agent-orchestrator)
  - [Position in sequence](#position-in-sequence-1)
  - [What it receives](#what-it-receives-1)
  - [Startup — what it loads before the crew runs](#startup-what-it-loads-before-the-crew-runs)
  - [3a. Planner Agent](#3a-planner-agent)
  - [3b. Analysis Agent](#3b-analysis-agent)
  - [3c. Processing Agent](#3c-processing-agent)
  - [What the orchestrator does after the crew finishes](#what-the-orchestrator-does-after-the-crew-finishes)
- [4. MCP Server — Analysis](#4-mcp-server-analysis)
  - [SSE response format (all tools)](#sse-response-format-all-tools)
  - [Tool: `extract_frames`](#tool-extract_frames)
  - [Tool: `detect_motion`](#tool-detect_motion)
  - [Tool: `detect_motion_sports`](#tool-detect_motion_sports)
  - [Tool: `detect_objects`](#tool-detect_objects)
  - [Tool: `read_asset`](#tool-read_asset)
  - [Tool: `analyze_scene` *(frontier)*](#tool-analyze_scene-frontier-calls-anthropic-api)
  - [Tool: `detect_objects_vision` *(frontier)*](#tool-detect_objects_vision-frontier-calls-anthropic-api)
  - [Tool: `transcribe_audio`](#tool-transcribe_audio)
- [5. MCP Server — Processing](#5-mcp-server-processing)
  - [Tool: `split_video`](#tool-split_video)
  - [Tool: `extract_clip`](#tool-extract_clip)
  - [Tool: `merge_clips`](#tool-merge_clips)
  - [Tool: `transform_video`](#tool-transform_video)
  - [Tool: `write_asset`](#tool-write_asset)
- [6. Data Flow Summary](#6-data-flow-summary)
- [7. Model Usage by Container](#7-model-usage-by-container)

---

## 1. Job Processing Sequence at a Glance

```
Browser
  └─ uploads video to Blob Storage (via api-gateway blob proxy)
       └─ Service Bus: VIDEO_UPLOADED
            └─ [1] preprocessing-worker  ← FFmpeg keyframe extraction
                 └─ PostgreSQL: video_keyframe_index
                 └─ Service Bus: VIDEO_INDEXED / VIDEO_UPLOADED status=indexed

User submits prompt (LibreChat → api-gateway)
  └─ Service Bus: JOB_QUEUED   ──or──   HTTP POST /run
       └─ [2] agent-orchestrator
            ├─ reads keyframe_index from PostgreSQL
            ├─ fetches tool catalogue from MCP servers
            │
            ├─ [2a] Planner Agent (Claude)
            │         └─ produces: extraction plan (JSON)
            │
            ├─ [2b] Analysis Agent (Claude + MCP tools)
            │         └─ calls ──► [3] mcp-server-analysis
            │         └─ produces: analysis results (JSON)
            │
            └─ [2c] Processing Agent (Claude + MCP tools)
                      └─ calls ──► [4] mcp-server-processing
                      └─ produces: output_url (Blob)

  └─ PostgreSQL: jobs.status = completed, output_url
  └─ Service Bus: JOB_COMPLETED
```

---

## 2. Preprocessing Worker — Keyframe Extraction

> **Not an AI container.** Runs FFmpeg and OpenCV. Documented here because its output is the primary input to every AI container in this pipeline.

**Source:** `backend/preprocessing-worker/app/processor.py`

### Position in sequence
Triggered by the `VIDEO_UPLOADED` Service Bus event immediately after the browser finishes uploading. Must complete before the agent-orchestrator can start. The orchestrator polls PostgreSQL until all videos reach `status='indexed'` (up to 5 minutes by default).

### What it receives

| Source | Data |
|---|---|
| Service Bus `VIDEO_UPLOADED` | `videoId`, `sessionId`, Blob URL of the uploaded video |
| Blob Storage | Raw video file (downloaded to a temp directory) |

### What it does

1. Calls `ffprobe` to get precise PTS (presentation timestamps) for every selected frame.
2. Runs FFmpeg with the filter `select='gt(scene,0.3)+eq(n,0)'` — selects every scene-change frame (scene score > 0.3) plus the very first frame of the video. Falls back to `fps=1` if this filter fails.
3. Extracts frames as JPEG files (`frame_%04d.jpg`).
4. Uploads each JPEG to Blob Storage under `videos/{user_id}/keyframes/`.
5. Inserts one row per frame into `video_keyframe_index`.
6. Updates `videos.status = 'indexed'`.
7. If a `session_id` was in the message, inserts an `uploaded_video` row in `session_assets`.
8. Publishes `VIDEO_INDEXED` to Service Bus.

### What it produces

**PostgreSQL `video_keyframe_index` rows:**
```json
{
  "frame_index": 0,
  "frame_url": "http://azurite:10000/devstoreaccount1/videos/.../keyframes/frame_0001.jpg",
  "timestamp_seconds": 0.0
}
```
One row per extracted frame. This is what every downstream analysis tool queries.

---

## 3. Agent Orchestrator

**Source:** `backend/agent-orchestrator/`
**Port:** 8001
**Entry points:** `POST /run` (HTTP, forwarded from api-gateway) and `job-queued` Service Bus queue
**Runtime:** Python + CrewAI, sequential process, three agents

### Position in sequence
The agent-orchestrator is the central AI coordinator. It receives a job after preprocessing is complete, orchestrates all three agents in sequence, and writes the final output back to Blob Storage and PostgreSQL.

### What it receives

**Via `POST /run` (HTTP):**
```json
{
  "prompt": "Extract all kitesurfing jumps and compile a highlight reel",
  "video_url": "http://azurite:10000/.../original/video.mp4",
  "video_urls": ["..."],
  "job_id": "uuid",
  "user_id": "uuid",
  "session_id": "uuid",
  "parent_job_id": "uuid or empty",
  "asset_urls": ["optional extra asset URLs"]
}
```

**Via Service Bus `JOB_QUEUED` message:**
```json
{
  "jobId": "uuid",
  "userId": "uuid",
  "prompt": "...",
  "videoUrl": "...",
  "videoIds": ["..."],
  "sessionId": "uuid",
  "parentJobId": "uuid or null"
}
```

### Startup — what it loads before the crew runs

`crew.py:run_crew()` runs all of the following concurrently before building agents:

| What | Where from | Used for |
|---|---|---|
| Keyframe index for each video | PostgreSQL `video_keyframe_index` | Passed to planner as context |
| Video duration (seconds) | `ffprobe` on primary video URL | Timestamp clamping in planner prompt |
| Tool catalogue | `GET /tools` from both MCP servers | Planner prompt + tool routing |
| Session assets (all files, videos, outputs in session) | PostgreSQL `session_assets` | Passed to planner |
| Parent job context (prior outputs) | PostgreSQL `jobs` + `outputs` | Follow-up job awareness |
| `agent_model` setting | PostgreSQL `app_settings` | Overrides env var default |
| `agent_rpm_limit` setting | PostgreSQL `app_settings` | Rate limiting per agent |

If the session has no assets yet (preprocessing still running), the orchestrator waits up to 30 seconds in 5-second intervals. If videos are not yet indexed, it waits up to 5 minutes.

### 3a. Planner Agent

**Role:** `"Video Extraction Planner"`
**Model:** Claude (configurable, default `anthropic/claude-sonnet-4-6`), temperature 0
**Has tools:** No — pure reasoning only

#### What it receives (as task description)
```
User prompt: "Extract all kitesurfing jumps..."

Videos (1):
  - http://azurite:10000/.../video.mp4

Primary video duration: 182.4 seconds. All timestamps must be clamped to [0, 182.4].

Existing session assets (2 items):
  - [uploaded_video] blob_url=... label=...
  - [uploaded_file] blob_url=... filename=markers.json content_type=application/json

Available tools:
  - extract_frames [analysis]: Return keyframe images from the pre-computed keyframe index.
    tags=[frames, keyframes] specialization=general default_model=none
  - detect_motion_sports [analysis]: Detect high-intensity sports motion events...
    tags=[motion, sports, events] specialization=sports default_model=local/yolo
  - analyze_scene [analysis]: Use a frontier vision model to semantically describe a scene...
    tags=[vision, frontier, scene, semantic] specialization=frontier_vision default_model=claude-vision
  - extract_clip [processing]: Extract a time-bounded clip from a video.
    ...
  (all 13 tools from both MCP servers)
```

The planner is instructed to call `read_asset` for any non-video uploaded files before planning, and to use vision tools (not YOLO) for image files.

#### What it produces (expected output)
A JSON object written to the task context for the next agent:
```json
{
  "videos": ["http://azurite:10000/.../video.mp4"],
  "segments": [
    {"start_seconds": 12.5, "end_seconds": 18.2, "reason": "high airtime jump", "video_url": "..."},
    {"start_seconds": 45.0, "end_seconds": 51.8, "reason": "trick sequence", "video_url": "..."}
  ],
  "selected_tools": [
    {"tool_name": "detect_motion_sports", "rationale": "sports domain, detects jump events"},
    {"tool_name": "extract_clip", "rationale": "extract identified segments"},
    {"tool_name": "merge_clips", "rationale": "compile highlight reel"}
  ],
  "operations": ["extract_clip for each segment", "merge_clips for final output"],
  "final_output_name": "kitesurfing_highlights.mp4"
}
```

---

### 3b. Analysis Agent

**Role:** `"Video Analysis Agent"`
**Model:** Claude (same as planner), temperature 0
**Has tools:** All tools from `mcp-server-analysis` (8 tools)

#### What it receives
- The planner's JSON output (via CrewAI's sequential context passing)
- Video URLs from the original request
- Task instruction: invoke each tool specified in `selected_tools` with the appropriate model alias if given; focus on the target segments identified in the plan; collect results across all videos into a unified analysis

The agent uses ReAct (Reason + Act) iteration — it reasons about which tool call to make next, calls the tool via the MCP client, receives the result, then decides the next step.

#### What it does
For each tool in `selected_tools`, the agent constructs the JSON input and calls `POST /tools/{name}/invoke` on `mcp-server-analysis` via the CrewAI tool wrapper. The SSE response stream is consumed until `status: done`.

Example tool call sequence:
```
1. extract_frames(video_url, frame_indices=[0,5,12,24,45,...])
   → {frames: [{frame_index, frame_url, timestamp_seconds}, ...]}

2. detect_motion_sports(video_url, segment_start_seconds=0, segment_end_seconds=182.4)
   → {events: [{start: 12.5, end: 18.2, score: 0.91}, ...], peak_motion_score: 0.91}

3. (optionally) analyze_scene(frame_url=".../frame_0013.jpg", model="claude-haiku")
   → {description: "...", objects: [...], activities: ["jump", "aerial"], setting: "ocean"}
```

#### What it produces (expected output)
```json
{
  "frame_urls": ["...frame_0000.jpg", "...frame_0005.jpg", "..."],
  "motion_scores": [
    {"start": 12.5, "end": 18.2, "score": 0.91, "event": "jump"},
    {"start": 45.0, "end": 51.8, "score": 0.87, "event": "trick"}
  ],
  "detected_objects": [...],
  "scene_descriptions": [...],
  "confirmed_segments": [
    {"start_seconds": 12.5, "end_seconds": 18.2, "video_url": "...", "confidence": 0.91},
    {"start_seconds": 45.0, "end_seconds": 51.8, "video_url": "...", "confidence": 0.87}
  ]
}
```
This JSON is passed as context to the Processing Agent.

---

### 3c. Processing Agent

**Role:** `"Video Processing Agent"`
**Model:** Claude (same model), temperature 0
**Has tools:** All tools from `mcp-server-processing` (5 tools)

#### What it receives
- The planner's extraction plan (via CrewAI context)
- The analysis agent's confirmed segment list
- `job_id` and `user_id` injected into task description

Task instruction: extract each confirmed segment as a clip, respecting the `video_url` per segment, then merge into a final highlight reel. If the plan calls for non-video outputs (analysis reports, structured data), use `write_asset`. Return the output blob URL.

#### What it does
Calls `POST /tools/{name}/invoke` on `mcp-server-processing` for each step. Typical sequence:
```
1. extract_clip(video_url, start_seconds=12.5, end_seconds=18.2, output_name="clip_001.mp4")
   → {clip_url: "http://azurite:10000/.../segments/clip_001.mp4"}

2. extract_clip(video_url, start_seconds=45.0, end_seconds=51.8, output_name="clip_002.mp4")
   → {clip_url: "http://azurite:10000/.../segments/clip_002.mp4"}

3. merge_clips(clip_urls=["...clip_001.mp4", "...clip_002.mp4"], output_name="kitesurfing_highlights.mp4")
   → {output_url: "http://azurite:10000/.../outputs/kitesurfing_highlights.mp4"}
```

#### What it produces
A plain string (or JSON) containing the final output blob URL:
```
http://azurite:10000/devstoreaccount1/videos/{user_id}/outputs/kitesurfing_highlights.mp4
```
Or as JSON for multi-output jobs:
```json
{
  "output_url": "http://azurite:10000/.../kitesurfing_highlights.mp4",
  "additional_outputs": [
    {"blob_url": "http://azurite:10000/.../analysis.json", "filename": "analysis.json"}
  ]
}
```

---

### What the orchestrator does after the crew finishes

1. Parses the output URL from the crew result string (handles plain URL or JSON).
2. If `session_id` is set: creates an `outputs` row and a `job_output_video` `session_assets` row in PostgreSQL.
3. Updates `jobs.status = 'completed'`, `jobs.output_url`.
4. Publishes `JOB_COMPLETED` to Service Bus with `{job_id, user_id, output_url, session_id}`.
5. Drains the internal log queue: writes all LLM call logs, agent step logs, and MCP tool call logs to `job_logs` in PostgreSQL.

On failure: sets `jobs.status = 'failed'`, publishes `JOB_FAILED`.

---

## 4. MCP Server — Analysis

**Source:** `mcp-servers/mcp-server-analysis/`
**Port:** 8100
**Protocol:** HTTP + SSE (`POST /tools/{name}/invoke` → `text/event-stream`)
**Called by:** Analysis Agent inside agent-orchestrator

The server is stateless. Every tool call is independent. The Analysis Agent discovers available tools at crew startup by calling `GET /tools`, which returns the full catalogue including `capability_tags`, `specialization`, `default_model`, and `available_models` for each tool.

### SSE response format (all tools)
```
data: {"status": "processing", "message": "Invoking detect_motion_sports..."}

data: {"status": "result", "result": { ... tool output ... }}

data: {"status": "done"}
```

### Tool: `extract_frames`
- **Reads from:** PostgreSQL `video_keyframe_index` (via `video_url` join on `videos.original_url`)
- **Input:** `{video_url, frame_indices?: [int], keyframe_index?: [...]}`
- **Output:** `{frames: [{frame_index, frame_url, timestamp_seconds}]}`
- **Note:** Does not re-process video. Returns pre-extracted frame URLs from the DB index. `frame_indices` filters which frames to return; omitting it returns all.

### Tool: `detect_motion`
- **Reads from:** Blob Storage (downloads video segment)
- **Input:** `{video_url, segment_start_seconds?, segment_end_seconds?}`
- **Output:** `{motion_score: float, high_motion_segments: [{start, end, score}]}`
- **Method:** OpenCV optical flow (general, not sports-tuned)

### Tool: `detect_motion_sports`
- **Reads from:** Blob Storage (downloads video segment)
- **Input:** `{video_url, segment_start_seconds?, segment_end_seconds?, sensitivity?: 0–1}`
- **Output:** `{events: [{start, end, score, event_type}], peak_motion_score: float}`
- **Method:** Sports-tuned optical flow — higher sensitivity to sudden directional changes (jumps, tricks). Model: `local/yolo`.

### Tool: `detect_objects`
- **Reads from:** Blob Storage (downloads frame images)
- **Input:** `{video_url, frame_urls: [string], object_classes?: [string]}`
- **Output:** `{detections: [{frame_url, class, confidence, bbox}]}`
- **Method:** YOLO general object detection on provided frame URLs. `object_classes` filters the result to specific COCO classes.

### Tool: `read_asset`
- **Reads from:** Blob Storage (any session asset URL)
- **Input:** `{blob_url: string, max_bytes?: int}`
- **Output:** `{content: string, content_type: string, size_bytes: int}`
- **Use case:** Called by the Planner Agent (via Analysis Agent) to read non-video session assets — JSON config files, CSV data, text prompts — before constructing the extraction plan. Default read limit: 1 MB.

### Tool: `analyze_scene` *(frontier — calls Anthropic API)*
- **Reads from:** Blob Storage (downloads frame image, then base64-encodes it)
- **Input:** `{frame_url: string, question?: string, model?: string}`
- **Output:** `{description: string, objects: [string], activities: [string], setting: string, model_used: string}`
- **Method:** Sends the frame as a base64-encoded image to a frontier vision model via LiteLLM. Default model alias: `claude-vision` → resolves to `anthropic/claude-opus-4-6` (overridable via `TOOL_FRONTIER_MODEL` env var or `tool_frontier_model` DB setting). Falls back to `claude-haiku` for lower-cost calls. **Use only when YOLO-style detection cannot answer the question.**

### Tool: `detect_objects_vision` *(frontier — calls Anthropic API)*
- **Reads from:** Blob Storage (downloads and base64-encodes frame)
- **Input:** `{frame_url: string, object_descriptions: [string], model?: string}`
- **Output:** `{detections: [{description, present: bool, confidence, notes}], model_used: string}`
- **Method:** Sends frame + natural-language object descriptions to a frontier vision model. Unlike YOLO, accepts arbitrary descriptions (e.g. `"kitesurfer mid-jump"`, `"person wearing red helmet"`). More expensive and slower than YOLO — only use when YOLO cannot identify the target.

### Tool: `transcribe_audio`
- **Reads from:** Blob Storage (downloads video for audio extraction)
- **Input:** `{video_url: string, language?: string (default "en")}`
- **Output:** `{transcript: string, segments: [{start, end, text}]}`
- **Method:** Local Whisper model. Useful when the prompt requires understanding speech (e.g. "extract all clips where the presenter says X").

---

## 5. MCP Server — Processing

**Source:** `mcp-servers/mcp-server-processing/`
**Port:** 8200
**Protocol:** HTTP + SSE
**Called by:** Processing Agent inside agent-orchestrator

All processing tools write their output to Blob Storage and return URLs. The server itself is stateless — no job state is held in memory.

### Tool: `split_video`
- **Reads from:** Blob Storage (downloads video)
- **Writes to:** Blob Storage (`videos/{user_id}/segments/`)
- **Input:** `{video_url: string, segment_length_seconds?: int (default 30)}`
- **Output:** `{segment_urls: [string]}`
- **Method:** FFmpeg — splits video into fixed-duration chunks. Used as a preliminary step when the agent wants to analyse a long video in smaller windows before making extraction decisions.

### Tool: `extract_clip`
- **Reads from:** Blob Storage (downloads source video)
- **Writes to:** Blob Storage (`videos/{user_id}/segments/`)
- **Input:** `{video_url: string, start_seconds: float, end_seconds: float, output_name?: string}`
- **Output:** `{clip_url: string}`
- **Method:** FFmpeg `-ss` seek + `-to` duration cut. This is the primary extraction tool — called once per segment identified by the analysis agent.

### Tool: `merge_clips`
- **Reads from:** Blob Storage (downloads each clip)
- **Writes to:** Blob Storage (`videos/{user_id}/outputs/`)
- **Input:** `{clip_urls: [string], output_name?: string}`
- **Output:** `{output_url: string}`
- **Method:** FFmpeg `concat` demuxer — concatenates clips in the provided order without re-encoding (stream copy). The output URL is what the processing agent returns as the final job result.

### Tool: `transform_video`
- **Reads from:** Blob Storage (downloads source video)
- **Writes to:** Blob Storage
- **Input:** `{video_url: string, operations: [{type, ...params}], output_name?: string}`
- **Output:** `{output_url: string}`
- **Supported operations:** resize (`width`, `height`), speed (`factor`), color grade (`filter_name`). Operations are applied as a single FFmpeg filter graph.
- **Use case:** Optional post-processing — called after `merge_clips` if the user prompt requests a specific output format (e.g. "square crop for Instagram", "2× speed").

### Tool: `write_asset`
- **Writes to:** Blob Storage (`assets/{session_id}/{uuid}/{filename}`)
- **Input:** `{content: string, filename: string, content_type?: string, session_id?: string}`
- **Output:** `{blob_url: string, filename: string, size_bytes: int}`
- **Use case:** Persists non-video outputs such as analysis reports (JSON), transcripts (TXT), or structured data (CSV). The processing agent uses this when the plan includes `additional_outputs` alongside the main video.

---

## 6. Data Flow Summary

```
PostgreSQL video_keyframe_index
  [{frame_index, frame_url, timestamp_seconds}]
               │
               ▼
      agent-orchestrator
         crew.kickoff(inputs={
           prompt,
           video_urls,
           video_duration_seconds,
           keyframe_index,          ← from DB
           session_assets,          ← from DB
           tool_catalogue,          ← from GET /tools
           job_id, user_id, session_id, parent_job_id
         })
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
 Planner    Analysis   Processing
  Agent      Agent       Agent
  (Claude)  (Claude     (Claude
            + tools)    + tools)
    │          │          │
    │          ▼          ▼
    │    POST /tools/    POST /tools/
    │    {name}/invoke   {name}/invoke
    │    → SSE stream    → SSE stream
    │          │          │
    │     mcp-server-  mcp-server-
    │      analysis    processing
    │      :8100        :8200
    │          │          │
    │          ▼          ▼
    │      Blob Storage reads/writes
    │      PostgreSQL keyframe reads
    │      Anthropic API (frontier tools)
    │
    └──── JSON plan ──► Analysis ──► JSON results ──► Processing ──► output_url
                                                               │
                                                               ▼
                                                    Blob Storage: outputs/{job}.mp4
                                                    PostgreSQL: jobs.output_url
                                                    Service Bus: JOB_COMPLETED
```

---

## 7. Model Usage by Container

| Container | Where | Model | Purpose |
|---|---|---|---|
| agent-orchestrator | Planner Agent | Claude (default: `claude-sonnet-4-6`) | Interprets prompt, selects tools, generates extraction plan |
| agent-orchestrator | Analysis Agent | Claude (same) | ReAct reasoning — decides which tool call to make next |
| agent-orchestrator | Processing Agent | Claude (same) | ReAct reasoning — decides clip/merge sequence |
| mcp-server-analysis | `analyze_scene` tool | frontier vision model (default: `claude-opus-4-6`) | Semantic scene understanding from keyframe images |
| mcp-server-analysis | `detect_objects_vision` tool | frontier vision model (same, or `claude-haiku`) | Open-vocabulary object detection from keyframe images |
| mcp-server-analysis | `detect_motion`, `detect_motion_sports` | local/OpenCV | Optical flow — no model API call |
| mcp-server-analysis | `detect_objects_*` (non-vision) | local/YOLO | Object detection — no model API call |
| mcp-server-analysis | `transcribe_audio` | local/Whisper | Audio transcription — no model API call |
| mcp-server-processing | All tools | — | FFmpeg operations — no model API call |

The agent model (Claude) is configurable via the `agent_model` setting in PostgreSQL `app_settings`, which takes precedence over the `AGENT_MODEL` environment variable. The frontier vision model is configurable via `tool_frontier_model` in `app_settings` or the `TOOL_FRONTIER_MODEL` env var, and can be changed at runtime without restarting the container.
