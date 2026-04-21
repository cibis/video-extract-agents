# External Agent — Claude Desktop

Standalone Docker Compose stack that connects Claude Desktop to the video extraction MCP tools.

One MCP server is registered in Claude Desktop:
- **`video-extraction-tools`** — MCP bridge (19 video analysis + processing tools, plus `get_upload_url` and `get_session_uploads`)

File upload is handled via a browser-based upload UI served by the bridge — no separate upload container or host-side script needed.

## Prerequisites

- Main stack running: `cd infrastructure/docker-compose && docker-compose up -d`
- Claude Desktop installed

## Setup

**1. Start the main stack:**
```bash
cd infrastructure/docker-compose
docker-compose up -d
```

**2. Start the Claude Desktop agent stack:**
```bash
cd external-agents/claude-desktop
bash scripts/start.sh
```

This builds and starts one container:
- `video-extract-cd-mcp-bridge` — MCP bridge (host port 8301) + upload HTTP API + upload browser UI

**3. Verify:**
```bash
curl http://localhost:8301/health
# Expected: {"status": "ok", "tools_loaded": 19}

docker ps --filter name=video-extract-cd-mcp-bridge
# Expected: container running

# Test upload UI
curl -s "http://localhost:8301/upload-ui?session=test" | grep -c upload
# Expected: non-zero
```

**4. Install Claude Desktop config:**

Windows (PowerShell):
```powershell
.\external-agents\claude-desktop\scripts\install.ps1
```

macOS:
```bash
./external-agents/claude-desktop/scripts/install.sh
```

**5. Restart Claude Desktop.**

**6. Verify:** The Tools icon should show `video-extraction-tools` with 21 tools.

## Usage

**With the preconfigured Project (recommended):**
1. Open the **Video Extraction** project in Claude Desktop.
2. Type your extraction prompt — agent instructions are already active.
3. The agent will call `get_upload_url` and give you a link to open in your browser.
4. Open the link, upload your video file(s), then tell the agent you're done.

**Without a Project:**
1. Start a new conversation.
2. Attach `external-agents/agent-instructions/video-extraction-agent.md` via the paperclip.
3. Type your extraction prompt.

## Video Upload Flow

```
Agent: get_upload_url(session_id, job_id)
  → returns {"upload_url": "http://localhost:8301/upload-ui?session=...&job=..."}
  → agent presents link to user

User: opens link in browser (new tab)
  → drag-and-drop or browse to select file(s)
  → each file uploads directly to the bridge → azurite:10000
  → page shows blob_url per file, stays open for repeat uploads

User: confirms upload complete

Agent: get_session_uploads(session_id)
  → returns [{"filename": "video.mp4", "blob_url": "http://localhost:10000/..."}]

Agent: ingest_video(source_url=blob_url, ...)
  → ingest_video remaps localhost:10000 → azurite:10000 internally
  → returns video_url + keyframe_index_asset

Agent: [runs 5-phase extraction pipeline]
  → output_url → reported to user
```

The upload UI is fully self-contained (no CDN, no external dependencies). Files can be
uploaded multiple times — the page never navigates away.

## Ports

| Service | Host port | Container |
|---|---|---|
| MCP bridge + upload API + upload UI | http://localhost:8301 | `video-extract-cd-mcp-bridge` |

Port 8301 avoids collision with the LibreChat external agent stack (8300).

## Skill Discovery

The `video-extraction-agent` skill is served by `video-extraction-tools` via MCP prompts.
Verify it is being served:

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"prompts/list","params":{}}\n' | \
  docker exec -i video-extract-cd-mcp-bridge python -m app.stdio_entry 2>/dev/null
# Expected: prompts[0].name == "video-extraction-agent"
```

### Preconfigure via a Claude Desktop Project (recommended)

1. Open Claude Desktop → **New Project**, name it **Video Extraction**.
2. Open **Project instructions** (pencil icon).
3. Paste the full contents of `external-agents/agent-instructions/video-extraction-agent.md`.
4. Ensure `video-extraction-tools` is enabled under **Tools**.

Every conversation in the project has agent instructions active automatically.

## Stop

```bash
cd external-agents/claude-desktop
docker-compose down
```

## Troubleshooting

| Issue | Fix |
|---|---|
| `tools_loaded: 0` on health check | `mcp-server-analysis` or `mcp-server-processing` not running — start main stack first |
| `docker exec` fails for `video-extract-cd-mcp-bridge` | Stack not started — run `scripts/start.sh` |
| Upload fails with 500 | Check `docker logs video-extract-cd-mcp-bridge` — Azurite may not be running |
| Upload UI not reachable | Container not started — run `scripts/start.sh` |
| `video-extract-network not found` | Main stack not running — `cd infrastructure/docker-compose && docker-compose up -d` |
| `get_session_uploads` returns empty list | Upload was not completed in browser before calling — confirm upload finished |
