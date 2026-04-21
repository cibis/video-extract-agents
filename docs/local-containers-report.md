# Local Container Architecture Report

## Table of Contents

- [1. Current Stack — All Containers](#1-current-stack-all-containers)
  - [Infrastructure Layer](#infrastructure-layer)
  - [Init Layer (one-shot, run before backend services start)](#init-layer-one-shot-run-before-backend-services-start)
  - [Backend Services Layer](#backend-services-layer)
  - [MCP Tool Servers Layer](#mcp-tool-servers-layer)
  - [Frontend Layer](#frontend-layer)
  - [Container Summary Table](#container-summary-table)

---

## 1. Current Stack — All Containers

The full local stack is defined in `infrastructure/docker-compose/docker-compose.yml` and runs 15 containers (13 long-running services plus 2 one-shot init containers) grouped into four layers, plus the init layer.

---

### Infrastructure Layer

#### `postgresql`
- **Image:** `postgres:15-alpine`
- **Port:** `5433` (host) → `5432` (container)
- **Role:** Primary relational database for all platform metadata — users, videos, jobs, keyframe index, session assets, and outputs. Every backend service reads from and writes to this database.
- **Local note:** Data is persisted in a named volume (`postgres-data`). The api-gateway uses a synchronous `pg` client; Python services use `asyncpg` via SQLAlchemy async.
- **Health-checked:** Yes (`pg_isready`)

#### `azurite`
- **Image:** `mcr.microsoft.com/azure-storage/azurite:latest`
- **Ports:** `10000` (Blob), `10001` (Queue), `10002` (Table)
- **Role:** Local emulator for Azure Blob Storage. Stores all media assets — uploaded videos, FFmpeg-extracted keyframes, intermediate video segments, and final compiled output videos. Every service that reads or writes video data connects to Azurite in local development instead of real Azure Blob Storage.
- **Local note:** Browser uploads and output downloads do **not** go directly to Azurite. Instead, the api-gateway exposes a blob proxy at `PUT /v1/blob-proxy/<path>` (upload) and `GET /v1/blob-proxy/<path>` (download). The proxy computes a manual SharedKey HMAC and pipes the request to Azurite — avoiding CORS and Azurite API-version compatibility issues. This proxy is only mounted when `OUTPUT_URL_MODE=local`. Blob data persists in a named volume (`azurite-data`).
- **Health-checked:** Yes (TCP connect on port 10000)

#### `mssql`
- **Image:** `mcr.microsoft.com/mssql/server:2022-latest`
- **Port:** Internal only (no host-side mapping)
- **Role:** SQL Server instance required by the Service Bus emulator as its backing persistence store. The servicebus-emulator container connects to this instance to store queue and message state. Not accessed directly by any application service.
- **Local note:** Data persists in a named volume (`mssql-data`). The Service Bus emulator will not start until this container is healthy.
- **Health-checked:** Yes (`sqlcmd SELECT 1`)

#### `servicebus-emulator`
- **Image:** `mcr.microsoft.com/azure-messaging/servicebus-emulator:latest`
- **Ports:** `5672` (AMQP), `5300` (management)
- **Role:** Local emulator for Azure Service Bus. Carries the five async lifecycle events that decouple the platform's services from one another:
  - `VIDEO_UPLOADED` → triggers preprocessing-worker
  - `VIDEO_INDEXED` → signals that keyframe index is ready
  - `JOB_QUEUED` → triggers agent-orchestrator's Service Bus consumer
  - `JOB_COMPLETED` → triggers notification-worker
  - `JOB_FAILED` → triggers notification-worker
- **Depends on:** `mssql` (healthy) — the emulator uses SQL Server as its persistence backend
- **Local note:** Without this emulator, the preprocessing-worker and notification-worker have no event source, and the agent-orchestrator's async consumer path cannot be tested. All services use the same shared access key for the emulator. Queue definitions are loaded from `./servicebus-config.json`.
- **Health-checked:** Disabled (`healthcheck: disable: true`) — dependent services use `condition: service_started`

#### `mongo`
- **Image:** `mongo:7`
- **Port:** Internal only (no host-side mapping)
- **Role:** MongoDB instance required by LibreChat. LibreChat uses MongoDB as its own internal datastore for conversation history, user sessions within the chat UI, and message storage. This is entirely separate from the platform's PostgreSQL database and is only accessed by the `librechat` container.
- **Local note:** Data persists in a named volume (`mongo-data`).
- **Health-checked:** Yes (`mongosh ping`)

---

### Init Layer (one-shot, run before backend services start)

#### `db-init`
- **Image:** `python:3.11-slim`
- **Role:** One-shot container that runs `scripts/init_db.py` to create all PostgreSQL tables and seed the local dev user (`00000000-0000-0000-0000-000000000001` / `dev@local`). Exits successfully when complete. The `api-gateway` and `preprocessing-worker` both wait for this container to finish (`condition: service_completed_successfully`) before starting.
- **Depends on:** `postgresql` (healthy)
- **Restart policy:** `no`

#### `storage-init`
- **Image:** `python:3.11-slim`
- **Role:** One-shot container that runs `scripts/init_storage.py` to create the `videos` Blob container in Azurite. Operation is idempotent. Exits successfully when complete. The `api-gateway` and `preprocessing-worker` both wait for this container to finish before starting.
- **Depends on:** `azurite` (healthy)
- **Restart policy:** `no`

---

### Backend Services Layer

#### `api-gateway`
- **Image:** Built from `backend/api-gateway/`
- **Port:** `8000`
- **Role:** The single external entry point for all client traffic. In local development it also acts as the development-mode auth bypass. Responsibilities:
  - Skips JWT validation (`LOCAL_DEV_SKIP_AUTH=true`) and injects a static local identity
  - Hosts the blob proxy (`PUT /v1/blob-proxy/<path>`, `GET /v1/blob-proxy/<path>`) so the browser never communicates with Azurite directly — the proxy computes SharedKey HMAC auth internally
  - Creates job records in PostgreSQL and publishes `JOB_QUEUED` to Service Bus
  - Proxies chat messages from LibreChat to the agent-orchestrator via `POST /run`
  - Serves real-time job progress over Server-Sent Events (SSE) by polling PostgreSQL
  - Returns blob-proxy URLs for output downloads (`OUTPUT_URL_MODE=local`) instead of signed Front Door URLs
- **Depends on:** `postgresql` (healthy), `db-init` (completed), `azurite` (healthy), `storage-init` (completed), `agent-orchestrator` (healthy)
- **Health-checked:** Yes (`/health` HTTP endpoint)

#### `agent-orchestrator`
- **Image:** Built from `backend/agent-orchestrator/`
- **Port:** `8001`
- **Role:** The AI brain of the platform. Hosts the CrewAI three-agent pipeline:
  - **Planner Agent** — receives the user prompt and keyframe index, generates an extraction plan, and selects MCP tools dynamically from the tool catalogue
  - **Analysis Agent** — calls `mcp-server-analysis` tools over SSE (motion detection, object detection, frame extraction, transcription)
  - **Processing Agent** — calls `mcp-server-processing` tools over SSE (clip extraction, merge, transform) and writes the compiled output to Azurite
  - All Claude model calls go through LiteLLM; default model is `anthropic/claude-sonnet-4-6` (overridable via `AGENT_MODEL` env var)
  - LiteLLM is configured for multiple providers: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and AWS Bedrock credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME`) are all injected
  - Accepts requests both via HTTP (`POST /run` from api-gateway) and via Service Bus consumer (`JOB_QUEUED` events)
  - After completion, updates the job record in PostgreSQL and publishes `JOB_COMPLETED` or `JOB_FAILED`
- **Depends on:** `postgresql` (healthy), `azurite` (healthy), `servicebus-emulator` (started), `mcp-server-analysis` (healthy), `mcp-server-processing` (healthy)
- **Requires:** `ANTHROPIC_API_KEY` set in `.env` (or alternative provider credentials)
- **Health-checked:** Yes

#### `preprocessing-worker`
- **Image:** Built from `backend/preprocessing-worker/`
- **Port:** None exposed (Service Bus–triggered, no inbound HTTP)
- **Role:** Listens on the `VIDEO_UPLOADED` Service Bus queue. When a new video is uploaded, it:
  1. Downloads the video from Azurite
  2. Runs FFmpeg to extract keyframes at 1fps (or scene-change boundaries)
  3. Generates a thumbnail strip
  4. Stores keyframe image URLs and the scene index in PostgreSQL (`video_keyframe_index` table)
  5. Publishes `VIDEO_INDEXED` to Service Bus, signalling the job queue that video analysis context is ready
- **Why it matters:** By reducing the full video to a set of indexed keyframe images before the agent sees it, this step dramatically cuts model token cost and latency. The planner agent receives a frame index instead of raw video bytes.
- **Depends on:** `postgresql` (healthy), `azurite` (healthy), `servicebus-emulator` (started), `db-init` (completed), `storage-init` (completed)

#### `notification-worker`
- **Image:** Built from `backend/notification-worker/`
- **Port:** None exposed (Service Bus–triggered, no inbound HTTP)
- **Role:** Listens on `JOB_COMPLETED` and `JOB_FAILED` Service Bus queues. When triggered, it:
  1. Fetches the user's email address from PostgreSQL
  2. Generates a signed download URL for the output video (blob-proxy URL locally, Front Door signed URL in production)
  3. Sends a transactional email with the original prompt, processing duration, download link, or failure reason
- **Local note:** `NOTIFICATION_MODE=stdout` — in local development this worker logs the email content to stdout instead of calling Azure Communication Services. No real email is sent.
- **Depends on:** `postgresql` (healthy), `servicebus-emulator` (started)

---

### MCP Tool Servers Layer

#### `mcp-server-analysis`
- **Image:** Built from `mcp-servers/mcp-server-analysis/`
- **Port:** `8100`
- **Role:** Read-only MCP tool server over SSE transport. Exposes video analysis tools consumed by the Analysis Agent in the orchestrator. Tools available:
  - `extract_frames` — returns pre-indexed keyframe images from Azurite
  - `detect_motion` / `detect_motion_sports` — optical flow motion scoring (general or sports-tuned)
  - `detect_objects` — YOLO-based general object detection
  - `analyze_scene` — frontier vision model semantic scene description (calls configured model API via LiteLLM)
  - `detect_objects_vision` — frontier vision model open-vocabulary object detection (calls configured model API via LiteLLM)
  - `transcribe_audio` — Whisper-based audio transcription
  - `read_asset` — reads non-video session assets (JSON, CSV, text) from Blob
- **Protocol:** Every tool is invoked via `POST /tools/{name}/invoke` returning `text/event-stream`; tool catalogue is fetched at orchestrator startup via `GET /tools`
- **Frontier model config:** `TOOL_FRONTIER_MODEL` (default `anthropic/claude-opus-4-6`) controls which model powers vision tools; overridable via `MODEL_ALIASES_OVERRIDE`. Supports Anthropic, OpenAI, and AWS Bedrock providers via LiteLLM.
- **Depends on:** `postgresql` (healthy), `azurite` (healthy)
- **Health-checked:** Yes

#### `mcp-server-processing`
- **Image:** Built from `mcp-servers/mcp-server-processing/`
- **Port:** `8200`
- **Role:** Output-producing MCP tool server over SSE transport. Exposes video processing tools consumed by the Processing Agent. Tools available:
  - `split_video` — splits a video into fixed-length segments stored in Azurite
  - `extract_clip` — extracts a time-bounded clip from a video
  - `merge_clips` — concatenates a list of clips into a final compiled output video
  - `transform_video` — applies resize, speed change, or colour grading to a clip
  - `write_asset` — persists generated non-video content (JSON, text, CSV) to Blob Storage
- **Protocol:** Same SSE-over-HTTP MCP pattern as the analysis server
- **Depends on:** `azurite` (healthy)
- **Health-checked:** Yes

---

### Frontend Layer

#### `angular-shell`
- **Image:** Built from `frontend/angular-shell/` (served by nginx; container port 80 mapped to host port 4200)
- **Port:** `4200`
- **Role:** The primary user interface. Hosts video upload (via the api-gateway blob proxy), the job progress dashboard, output video preview and download, and the LibreChat iframe embed. Communicates with the api-gateway over REST and SSE. Exchanges job lifecycle events with the LibreChat iframe via the `window.postMessage` API.
- **Depends on:** `api-gateway` (healthy)

#### `librechat`
- **Image:** Built from `frontend/librechat/` (project fork)
- **Port:** `3080`
- **Role:** Chat interface embedded inside the Angular shell via iframe. Users type natural language prompts here. The fork customises LibreChat to:
  - Route all chat requests to `POST /v1/chat` on the api-gateway (instead of upstream LibreChat model endpoints)
  - Disable model selector, file upload UI, and parameter controls
  - Emit `JOB_SUBMITTED` / `JOB_COMPLETED` postMessage events to the Angular shell when the api-gateway response includes an `x-job-id` header
  - Apply platform branding (in `client/src/platform/`)
- **Session provisioning:** `ALLOW_REGISTRATION=true` and `ALLOW_UNVERIFIED_EMAIL=true` are set so the Angular shell can automatically provision a LibreChat user for `dev@local` via `POST /api/auth/register` on startup. Users never see the registration UI — `platform-init.js` bootstraps the LibreChat session from URL parameters before React hydrates.
- **CORS:** `ANGULAR_ORIGIN=http://localhost:4200` is set so LibreChat's CORS policy allows the Angular shell origin, enabling the browser to store the HttpOnly refresh token cookie set on LibreChat login.
- **Uses MongoDB** (`mongo` container) for its own conversation and session state — separate from the platform's PostgreSQL
- **Depends on:** `api-gateway` (healthy), `mongo` (healthy)

---

### Container Summary Table

| Container | Layer | Port | Triggered by | Depends on |
|---|---|---|---|---|
| `postgresql` | Infrastructure | 5433 | Always up | — |
| `azurite` | Infrastructure | 10000–10002 | Always up | — |
| `mssql` | Infrastructure | internal | Always up | — |
| `servicebus-emulator` | Infrastructure | 5672, 5300 | Always up | mssql |
| `mongo` | Infrastructure | internal | Always up | — |
| `db-init` | Init (one-shot) | — | On startup | postgresql |
| `storage-init` | Init (one-shot) | — | On startup | azurite |
| `api-gateway` | Backend | 8000 | HTTP | postgresql, db-init, azurite, storage-init, agent-orchestrator |
| `agent-orchestrator` | Backend | 8001 | HTTP + Service Bus | postgresql, azurite, servicebus-emulator, mcp-server-analysis, mcp-server-processing |
| `preprocessing-worker` | Backend | — | Service Bus (`VIDEO_UPLOADED`) | postgresql, azurite, servicebus-emulator, db-init, storage-init |
| `notification-worker` | Backend | — | Service Bus (`JOB_COMPLETED`, `JOB_FAILED`) | postgresql, servicebus-emulator |
| `mcp-server-analysis` | MCP Tools | 8100 | HTTP (SSE) | postgresql, azurite |
| `mcp-server-processing` | MCP Tools | 8200 | HTTP (SSE) | azurite |
| `angular-shell` | Frontend | 4200 | HTTP | api-gateway |
| `librechat` | Frontend | 3080 | HTTP | api-gateway, mongo |

**Total: 15 containers** (13 long-running + 2 one-shot init)

---

---
