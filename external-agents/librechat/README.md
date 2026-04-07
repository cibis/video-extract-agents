# External Agent — LibreChat (Official Image)

Standalone LibreChat stack that connects to the video extraction MCP tools via the MCP bridge.

## Prerequisites

- Main stack running: `cd infrastructure/docker-compose && docker-compose up -d`
- Anthropic API key

## Setup

```bash
cp .env.example .env
# Set ANTHROPIC_API_KEY in .env

./scripts/start.sh
```

## Usage

1. Open http://localhost:3081 and create an account.
2. Start a new chat.
3. Click the paperclip → attach `agent-instructions/video-extraction-agent.md`.
4. Click the paperclip again → attach your video file.
5. Type your extraction prompt (e.g. "Extract all kitesurfing jumps and compile into a highlight reel").

The agent reads the instructions file, ingests the video via `ingest_video`, and runs the full 5-phase extraction pipeline automatically.

## How it works

```
LibreChat                     MCP Bridge :8300          Existing tool servers
────────────────              ─────────────────         ──────────────────────
Attaches video file  ──────▶  translates MCP↔SSE  ──▶  mcp-server-analysis :8100
Agent calls tools             tools/list                mcp-server-processing :8200
via MCP/SSE protocol          tools/call
```

Video files attached in LibreChat are stored locally and served at
`http://librechat-official:3080/api/files/{id}/filename.mp4`. The `ingest_video` tool
downloads from this URL (same Docker network), uploads to Azurite, runs FFmpeg keyframe
extraction, and returns `video_url` + `keyframe_index_asset` to start the pipeline.

## Ports

| Service | Port |
|---|---|
| LibreChat | http://localhost:3081 |
| MCP Bridge | http://localhost:8300 |

## Stop

```bash
docker-compose down
```
