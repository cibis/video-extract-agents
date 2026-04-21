# Setup Guide — Video Extract Platform

This guide walks through the full setup of the platform on a **Windows machine with Docker Desktop**, including GitLab, GitHub, and Azure configuration from scratch.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Clone the repository](#2-clone-the-repository)
3. [GitLab setup](#3-gitlab-setup)
4. [GitHub mirror setup](#4-github-mirror-setup)
5. [Azure — one-time account setup](#5-azure--one-time-account-setup)
6. [Azure — Entra External ID (auth)](#6-azure--entra-external-id-auth)
7. [Azure — dev and prod environments (Terraform)](#7-azure--dev-and-prod-environments-terraform)
8. [Local development bootstrap](#8-local-development-bootstrap)
9. [Running unit tests](#9-running-unit-tests)
10. [Running integration tests locally](#10-running-integration-tests-locally)
11. [First deployment to Azure](#11-first-deployment-to-azure)
12. [LibreChat fork setup](#12-librechat-fork-setup)
13. [External agents (LibreChat official / Claude Desktop)](#13-external-agents-librechat-official--claude-desktop)
14. [GitLab CI/CD variables reference](#14-gitlab-cicd-variables-reference)
15. [Secrets reference (all services)](#15-secrets-reference-all-services)
16. [Troubleshooting on Windows](#16-troubleshooting-on-windows)

---

## 1. Prerequisites

Install the following on your Windows machine before proceeding.

| Tool | Version | Download |
|---|---|---|
| Docker Desktop | ≥ 4.30 | https://www.docker.com/products/docker-desktop/ |
| WSL 2 (Ubuntu) | latest | `wsl --install` in PowerShell (Admin) |
| Git | ≥ 2.45 | https://git-scm.com/download/win |
| Node.js | 22 LTS | https://nodejs.org/ |
| Python | 3.11 | https://www.python.org/downloads/ |
| Poetry | 1.8.x | `pip install poetry==1.8.3` |
| Angular CLI | 19 | `npm install -g @angular/cli@19` |
| Terraform | ≥ 1.6 | https://developer.hashicorp.com/terraform/install |
| Azure CLI | ≥ 2.63 | https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows |
| GitLab CLI (`glab`) | latest | https://gitlab.com/gitlab-org/cli |

### Docker Desktop configuration (Windows)

1. Open Docker Desktop → Settings → Resources → WSL Integration
2. Enable integration with your Ubuntu WSL distro
3. Settings → General → enable "Use WSL 2 based engine"
4. Allocate at least **8 GB RAM** and **4 CPUs** to Docker
5. Settings → Resources → Disk image size — allocate at least **20 GB**. The `mcp-server-analysis` image includes PyTorch (pulled in by the `ultralytics` object-detection dependency), which adds ~500–800 MB to that image alone.

---

## 2. Clone the repository

```bash
git clone https://gitlab.com/your-org/video-extract-agents.git
cd video-extract-agents
```

---

## 3. GitLab setup

### 3.1 Create the project

1. Log in to [gitlab.com](https://gitlab.com) (or your self-hosted GitLab)
2. Create a new project: **New project → Create blank project**
   - Name: `video-extract-agents`
   - Visibility: Private
   - Do NOT initialise with README (the repo already has content)

### 3.2 Push the code

```bash
git remote set-url origin https://gitlab.com/YOUR-ORG/video-extract-agents.git
git push -u origin main
```

### 3.3 Enable the Container Registry

GitLab Settings → Packages and registries → Container Registry → Enable

### 3.4 Set CI/CD variables

Go to **Settings → CI/CD → Variables** and add the following (all masked, not protected unless noted):

| Variable | Description |
|---|---|
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_TENANT_ID` | Azure AD tenant ID for service principal |
| `AZURE_CLIENT_ID` | Service principal app ID |
| `AZURE_CLIENT_SECRET` | Service principal secret |
| `DB_ADMIN_PASSWORD` | PostgreSQL admin password |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `ENTRA_TENANT_ID` | Entra External ID tenant ID |
| `ENTRA_CLIENT_ID` | Entra app registration client ID |

> **ACR credentials** are read from Terraform outputs during CI — no need to add them as GitLab variables.

### 3.5 Create a service principal for CI

```bash
az login
az ad sp create-for-rbac \
  --name "video-extract-ci" \
  --role Contributor \
  --scopes /subscriptions/<SUBSCRIPTION_ID> \
  --output json
```

Note the `appId` (→ `AZURE_CLIENT_ID`), `password` (→ `AZURE_CLIENT_SECRET`), and `tenant` (→ `AZURE_TENANT_ID`).

---

## 4. GitHub mirror setup

### 4.1 Create the GitHub repository

1. Go to [github.com](https://github.com) → New repository
2. Name: `video-extract-agents`, Private, **no README**

### 4.2 Configure push mirroring in GitLab

1. GitLab project → Settings → Repository → Mirroring repositories
2. **Push** direction
3. URL: `https://github.com/YOUR-ORG/video-extract-agents.git`
4. Authentication: GitHub Personal Access Token (PAT) with `repo` scope
5. Enable **Mirror repository**

GitLab will push to GitHub automatically on every push.

---

## 5. Azure — one-time account setup

### 5.1 Required resource groups (manual, one-time)

```bash
az login

# Terraform state storage (one account, three state keys — dev/prod/test)
az group create --name terraform-state-rg --location eastus

az storage account create \
  --name tfstatevideoextract \
  --resource-group terraform-state-rg \
  --sku Standard_LRS \
  --kind StorageV2 \
  --min-tls-version TLS1_2

az storage container create \
  --name tfstate \
  --account-name tfstatevideoextract

# Retrieve the access key (needed for TF_STATE_ACCESS_KEY CI variable and bootstrap-dev.sh)
az storage account keys list \
  --account-name tfstatevideoextract \
  --resource-group terraform-state-rg \
  --query "[0].value" -o tsv
```

### 5.2 Azure Container Registry

The ACR is provisioned by Terraform (`modules/acr/`) when you run `terraform apply` for each environment. No manual creation is needed.

---

## 6. Azure — Entra External ID (auth)

### 6.1 Create an External ID tenant

1. Azure Portal → Azure Active Directory → Manage tenants → Create
2. Select **External** → follow the wizard
3. Note the **tenant ID** → `ENTRA_TENANT_ID`

### 6.2 Register the API application

1. In the External ID tenant → App registrations → New registration
2. Name: `video-extract-api`
3. Supported account types: Accounts in this organizational directory only
4. Redirect URI: (leave blank for API)
5. After creation, note the **Application (client) ID** → `ENTRA_CLIENT_ID`
6. Expose an API → Add a scope (e.g. `api.access`)

### 6.3 Register the frontend application

1. New registration: `video-extract-spa`
2. Redirect URI: `http://localhost:4200` (dev) + your production URL
3. Under Authentication → enable Access tokens and ID tokens

### 6.4 Configure magic link user flow

1. External ID tenant → User flows → New user flow
2. Type: **Sign up and sign in**
3. Identity providers: Email one-time passcode
4. Follow wizard — this is the magic link flow

---

## 7. Azure — dev and prod environments (Terraform)

Both environments are provisioned using one-time bootstrap scripts that handle
the two-phase Terraform apply (ACR must exist before container apps can be created).

All credentials live in a single gitignored file — copy the committed template, fill it in once, then run either script:

```bash
# Copy the template (committed, no real secrets) and fill in your values
cp scripts/credentials.sh.example scripts/credentials.sh
# Edit scripts/credentials.sh — the filled-in copy is gitignored

bash scripts/bootstrap-dev.sh    # provisions video-extract-dev
```

The bootstrap script:
- Sources `scripts/credentials.sh` for all Azure/Terraform credentials
- Phase 1: create ACR only (`-target=module.acr`)
- Pushes placeholder images so container apps can be created immediately
- Phase 2: full `terraform apply`
- Logs all Terraform output to `gitlab-logs/terraform/bootstrap-dev-<timestamp>.log`

This creates:
- Resource group `video-extract-dev`
- Azure Container Registry (Basic)
- Azure Container Apps environment + all 8 container apps
- Blob Storage (LRS)
- Service Bus namespace (Standard) + 3 queues
- Application Insights
- Azure Front Door (Standard)
- Azure Key Vault (secrets injected; purged immediately on destroy)

**After bootstrap, get the UI URL:**

```bash
az containerapp show --name angular-shell \
  --resource-group video-extract-dev \
  --query "properties.configuration.ingress.fqdn" --output tsv
```

---

## 8. Local development bootstrap

### 8.1 Copy .env files

For each service, copy `.env.example` to `.env`:

```bash
# Windows PowerShell
Get-ChildItem -Recurse -Filter ".env.example" | ForEach-Object {
    $dest = Join-Path $_.DirectoryName ".env"
    if (-not (Test-Path $dest)) { Copy-Item $_.FullName $dest }
}
```

Or manually:
```bash
cp backend/api-gateway/.env.example              backend/api-gateway/.env
cp backend/agent-orchestrator/.env.example       backend/agent-orchestrator/.env
cp backend/preprocessing-worker/.env.example     backend/preprocessing-worker/.env
cp mcp-servers/mcp-server-analysis/.env.example  mcp-servers/mcp-server-analysis/.env
cp mcp-servers/mcp-server-processing/.env.example mcp-servers/mcp-server-processing/.env
cp frontend/librechat/.env.example               frontend/librechat/.env
```

Edit `backend/agent-orchestrator/.env` and set your `ANTHROPIC_API_KEY`.

### 8.2 Start the local stack

```bash
cd infrastructure/docker-compose
docker compose up --build
```

> **Note:** The first build of `mcp-server-analysis` is slower than the other services because it downloads the PyTorch CPU wheel via the `ultralytics` dependency. Subsequent builds use the Docker layer cache and are fast.

Services start on:
- Angular Shell: http://localhost:4200
- LibreChat: http://localhost:3080
- API Gateway: http://localhost:8000
- Agent Orchestrator: http://localhost:8001
- MCP Analysis: http://localhost:8100
- MCP Processing: http://localhost:8200
- Azurite (Blob): http://localhost:10000
- PostgreSQL: localhost:5433

### 8.3 Database and storage initialisation

**The database schema and Azurite blob storage container are initialised automatically** by `db-init` and `storage-init` containers in Docker Compose. You do not need to run `scripts/init_db.py` or `scripts/init_storage.py` manually for local development.

> Manual scripts (`scripts/init_db.py`, `scripts/init_storage.py`) are used for Azure deployments (see §11.3).

### 8.4 Create Service Bus queues (local emulator)

Service Bus queues are **not** auto-created by Docker Compose. Run this once after the stack is up:

```bash
# Connection string for the local Service Bus emulator
export SERVICE_BUS_CONNECTION_STRING="Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"

pip install azure-servicebus
python scripts/create_service_bus_queues.py
```

### 8.5 Verify everything is running

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"api-gateway"}

curl http://localhost:8001/health
# {"status":"ok","service":"agent-orchestrator"}

curl http://localhost:8100/tools
# [{"name":"extract_frames",...},...]

curl http://localhost:8200/tools
# [{"name":"split_video",...},...]
```

---

## 9. Running unit tests

### Node.js (API Gateway)

```bash
cd backend/api-gateway
npm ci
npm test
# or with coverage:
npm run test:coverage
```

### Python services

```bash
# Agent Orchestrator
cd backend/agent-orchestrator
poetry install
poetry run pytest tests/unit/ -v

# Preprocessing Worker
cd backend/preprocessing-worker
poetry install
poetry run pytest tests/unit/ -v

# MCP Server Analysis
cd mcp-servers/mcp-server-analysis
poetry install
poetry run pytest tests/unit/ -v

# MCP Server Processing
cd mcp-servers/mcp-server-processing
poetry install
poetry run pytest tests/unit/ -v
```

---

## 10. Running integration tests locally

Requires the full local stack to be running.

```bash
# Convenience script (WSL/Git Bash)
bash scripts/run-integration-local.sh

# Or manually:
cd infrastructure/docker-compose
docker compose up -d --build

cd ../..
pip install pytest httpx pytest-asyncio
pytest tests/integration/ -v -m integration
```

Integration tests require an Anthropic API key (`ANTHROPIC_API_KEY`) in the agent-orchestrator environment.

---

## 11. First deployment to Azure

### 11.1 Trigger the CI pipeline

After the bootstrap scripts provision the infrastructure (§7), the container apps are running
placeholder images. The real application images are built and pushed by the GitLab CI pipeline.

Push a commit to `main` (or trigger the pipeline manually in GitLab) — the pipeline will:
1. Build all 7 Docker images tagged with the commit SHA
2. Run tests against an ephemeral Azure test environment
3. Push images to ACR and deploy to `video-extract-dev`

### 11.2 Initialise the Azure database

The PostgreSQL container starts empty on first deploy. Run `init_db.py` once to create all tables.

```bash
# Exec into the postgresql container app to confirm it is running
az containerapp exec \
  --name postgresql \
  --resource-group video-extract-dev \
  --command "psql -U psqladmin -d videoextract -c '\dt'"

# Run init_db.py from a machine that has network access to the ACA environment,
# or from a one-off az containerapp exec session:
DB_PASS=$(cd infrastructure/terraform/envs/dev && terraform output -raw db_admin_password 2>/dev/null)
DATABASE_URL="postgresql://psqladmin:${DB_PASS}@<postgresql-internal-fqdn>:5432/videoextract" \
  python scripts/init_db.py
python scripts/create_service_bus_queues.py
```

> The internal FQDN for the `postgresql` container app is shown in the Azure portal under the ACA environment → Apps → postgresql → Overview. Other services inside the same environment reach it simply as `postgresql:5432`.

### 11.4 Subsequent deploys via CI

Push to `main` — the GitLab CI pipeline runs automatically:
- Lint → Unit tests → Build → Integration tests → Test env E2E → Push to ACR → Deploy to dev

Production deploy requires a manual approval step in the pipeline.

---

## 12. LibreChat fork setup

The `frontend/librechat/` directory contains only the platform config files. You need to fork the upstream LibreChat repository and overlay these files.

### 12.1 Set up the fork

```bash
# One-time setup
cd frontend/librechat

# If starting fresh: clone upstream into this directory
git clone https://github.com/danny-avila/LibreChat.git .
git remote rename origin upstream

# Add your GitLab remote
git remote add origin https://gitlab.com/YOUR-ORG/video-extract-agents.git

# The platform files (librechat.yaml, Dockerfile, client/src/platform/) are already
# committed in the platform repo — they overlay the upstream files.
```

### 12.2 Keeping in sync with upstream

```bash
cd frontend/librechat
git fetch upstream
git merge upstream/main --no-ff -m "chore: merge upstream LibreChat <version>"
# Resolve any conflicts — our changes are isolated to client/src/platform/ and root config files
git push origin main
```

### 12.3 Mount the JobBridge component

In the LibreChat root app component (typically `client/src/App.tsx` or similar), add:

```tsx
import JobBridge from './platform/JobBridge';

// Inside the component JSX:
<JobBridge />
```

---

## 13. External agents (LibreChat official / Claude Desktop)

The `external-agents/` directory provides two ways to connect standard MCP agents directly to the platform's tool servers — without going through the normal Angular/LibreChat frontend.

Both paths share the same **MCP bridge** service (port 8300) that translates standard MCP JSON-RPC ↔ the platform's custom HTTP+SSE tool protocol.

### 13.1 Prerequisites

- Main local stack running: `cd infrastructure/docker-compose && docker compose up -d`
- `mcp-server-analysis` and `mcp-server-processing` must be healthy (the bridge waits for them)

Verify the main stack is up:
```bash
curl http://localhost:8100/tools | python -m json.tool | head -20
curl http://localhost:8200/tools | python -m json.tool | head -20
```

---

### 13.2 LibreChat (official image)

Runs the official LibreChat Docker image (not the forked one) on port 3081, connected to the MCP bridge via SSE transport. Users attach the agent instructions file and a video file directly in chat.

**1. Copy the env file and set your API key:**
```bash
cp external-agents/librechat/.env.example external-agents/librechat/.env
# Edit .env — set ANTHROPIC_API_KEY and generate random values for the secrets below
```

Required values in `external-agents/librechat/.env`:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (`sk-ant-api03-...`) |
| `LIBRECHAT_SECRET_KEY` | Random 32-char string (e.g. `openssl rand -base64 32`) |
| `CREDS_KEY` | Random 64-char hex string (e.g. `openssl rand -hex 32`) |
| `CREDS_IV` | Random 32-char hex string (e.g. `openssl rand -hex 16`) |
| `JWT_SECRET` | Random string |
| `JWT_REFRESH_SECRET` | Random string |

**2. Start LibreChat + MCP bridge:**
```bash
cd external-agents/librechat
docker compose up -d
```

This starts:
- `video-extract-mcp-bridge` (port 8300) — MCP bridge connecting to analysis/processing servers
- `video-extract-librechat-official` (port 3081) — LibreChat official image
- `video-extract-mongo-official` — MongoDB for LibreChat

**3. Verify the bridge:**
```bash
curl http://localhost:8300/health
# Expected: {"status":"ok","tools_loaded":19}
# (18 original tools + ingest_video)
```

**4. Open LibreChat and start a session:**
1. Go to http://localhost:3081 and create an account
2. Start a new conversation
3. Click the paperclip → attach `external-agents/agent-instructions/video-extraction-agent.md`
4. In the same message (or next): attach your video file
5. Type your extraction prompt (e.g. *"Extract all kitesurfing jumps from this video and compile into a highlight reel"*)

The agent will use `ingest_video` to index the video (LibreChat serves uploaded files over HTTP on the same Docker network), then run the full 5-phase extraction pipeline.

**Stop the LibreChat stack:**
```bash
cd external-agents/librechat
docker compose down
```

---

### 13.3 Claude Desktop

Claude Desktop uses the MCP bridge via **stdio transport** (`docker exec`). It also needs the **Azure Storage MCP server** (`@azure/mcp`) to upload video files to Azurite before calling `ingest_video`.

**Prerequisites:**
- [Claude Desktop](https://claude.ai/download) installed
- Node.js installed (`npx` required for `@azure/mcp`)

**1. Start the MCP bridge (from the main docker-compose profile):**
```bash
# From repo root (Git Bash / WSL):
bash external-agents/claude-desktop/scripts/start-mcp-bridge.sh
```

Or manually:
```bash
cd infrastructure/docker-compose
docker compose --profile external-agents up mcp-bridge -d
```

**2. Verify the bridge:**
```bash
curl http://localhost:8300/health
# Expected: {"status":"ok","tools_loaded":19}
```

**3. Install the Claude Desktop config:**

Windows (PowerShell):
```powershell
.\external-agents\claude-desktop\scripts\install.ps1
```

macOS:
```bash
bash external-agents/claude-desktop/scripts/install.sh
```

This copies `external-agents/claude-desktop/config/claude_desktop_config.json` to the Claude Desktop config directory, configuring two MCP servers:
- `video-extraction-tools` — the MCP bridge via `docker exec ... python -m app.stdio_entry`
- `azure-storage` — `@azure/mcp` pointed at local Azurite (`localhost:10000`)

**4. Restart Claude Desktop.**

**5. Verify:** The Tools icon in Claude Desktop should show both `video-extraction-tools` (19 tools) and `azure-storage`.

**6. Start a session:**
1. Start a new conversation
2. Attach `external-agents/agent-instructions/video-extraction-agent.md`
3. Attach your video file (Claude Desktop provides the local file path)
4. Type your extraction prompt

The agent will use `azure-storage` to upload the video to Azurite, then call `ingest_video` with the resulting blob URL, then run the full extraction pipeline.

**Test the stdio bridge manually:**
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \
  docker exec -i video-extract-mcp-bridge python -m app.stdio_entry
# Should return a list of 19 tools including ingest_video
```

---

### 13.4 The `ingest_video` tool

Both external agent paths call `ingest_video` as Phase 0 of the pipeline. The tool:

1. Downloads the video from the source URL (remapping `localhost:10000` → `azurite:10000` for container access)
2. Uploads the original to Blob Storage under `videos/external/{scope}/original/`
3. Extracts keyframes with FFmpeg (1.5 fps + scene change detection)
4. Uploads keyframe images and writes the keyframe index JSON blob
5. Inserts rows into `videos`, `video_keyframe_index`, and optionally `session_assets`

Returns `video_url`, `keyframe_index_asset`, `session_id`, `video_id`, `frame_count`, and `duration_seconds`. The agent passes `keyframe_index_asset` directly to `extract_frames` to begin the 5-phase pipeline.

All DB writes use the local dev user UUID `00000000-0000-0000-0000-000000000001` (no auth context for external agents).

---

## 14. GitLab CI/CD variables reference

All variables are set in **Settings → CI/CD → Variables** (masked).

| Variable | Required | Description |
|---|---|---|
| `AZURE_SUBSCRIPTION_ID` | Yes | Azure subscription ID |
| `AZURE_TENANT_ID` | Yes | Azure AD tenant for service principal |
| `AZURE_CLIENT_ID` | Yes | Service principal app ID |
| `AZURE_CLIENT_SECRET` | Yes | Service principal secret |
| `DB_ADMIN_PASSWORD` | Yes | PostgreSQL admin password (→ `TF_VAR_db_admin_password`) |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key (`sk-ant-api03-...`) (→ `TF_VAR_anthropic_api_key`) |
| `ENTRA_TENANT_ID` | Yes | Entra External ID tenant ID (→ `TF_VAR_entra_tenant_id`) |
| `ENTRA_CLIENT_ID` | Yes | Entra API app registration client ID (→ `TF_VAR_entra_client_id`) |
| `IMAGE_TAG` | Yes | Docker image tag to deploy (→ `TF_VAR_image_tag`) |

> **ACR credentials** (`ACR_SERVER`, `ACR_USERNAME`, `ACR_PASSWORD`) are no longer CI variables — the ACR is provisioned by Terraform and credentials are read from Terraform outputs during the build stage.

---

## 15. Secrets reference (all services)

### API Gateway (`backend/api-gateway/.env`)

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL sync URL (`postgresql://...`) |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage connection string |
| `SERVICE_BUS_CONNECTION_STRING` | Service Bus connection string |
| `AGENT_ORCHESTRATOR_URL` | URL of agent-orchestrator (`http://agent-orchestrator:8001`) |
| `LOCAL_DEV_SKIP_AUTH` | `true` locally; must be absent in CI/prod |
| `ENTRA_TENANT_ID` | Entra External ID tenant ID |
| `ENTRA_CLIENT_ID` | Entra app registration client ID |
| `OUTPUT_URL_MODE` | `local` or `frontdoor` |
| `FRONT_DOOR_URL` | Azure Front Door endpoint hostname (CI/prod) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights (omit locally) |

### Agent Orchestrator (`backend/agent-orchestrator/.env`)

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL asyncpg URL (`postgresql+asyncpg://...`) |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage connection string |
| `SERVICE_BUS_CONNECTION_STRING` | Service Bus connection string |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `AGENT_MODEL` | LiteLLM model string for agent reasoning (e.g. `anthropic/claude-sonnet-4-6`) |
| `TOOL_FRONTIER_MODEL` | LiteLLM model string for vision tools (e.g. `anthropic/claude-opus-4-6`) |
| `MCP_ANALYSIS_URL` | `http://mcp-server-analysis:8100` |
| `MCP_PROCESSING_URL` | `http://mcp-server-processing:8200` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights (omit locally) |

### Preprocessing Worker (`backend/preprocessing-worker/.env`)

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL asyncpg URL |
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage connection string |
| `SERVICE_BUS_CONNECTION_STRING` | Service Bus connection string |
| `KEYFRAME_FPS` | Keyframe extraction rate (default: `1`) |

### MCP Server Analysis (`mcp-servers/mcp-server-analysis/.env`)

| Variable | Description |
|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage connection string |
| `ANTHROPIC_API_KEY` | Anthropic API key (required for `analyze_scene` and `detect_objects_vision` frontier tools) |
| `TOOL_FRONTIER_MODEL` | LiteLLM model string for vision tools (default: `anthropic/claude-opus-4-6`) |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights (omit locally) |

### MCP Server Processing (`mcp-servers/mcp-server-processing/.env`)

| Variable | Description |
|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | Blob Storage connection string |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | App Insights (omit locally) |

### MCP Bridge (`external-agents/mcp-bridge`)

Set via `docker-compose.yml` environment block (no separate `.env` file needed for local dev).

| Variable | Default | Description |
|---|---|---|
| `MCP_ANALYSIS_URL` | `http://mcp-server-analysis:8100` | Analysis MCP server URL |
| `MCP_PROCESSING_URL` | `http://mcp-server-processing:8200` | Processing MCP server URL |
| `LOG_LEVEL` | `INFO` | Uvicorn log level |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | (omit locally) | App Insights connection string |

### LibreChat official (`external-agents/librechat/.env`)

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key (`sk-ant-api03-...`) |
| `LIBRECHAT_SECRET_KEY` | 32-char random string — `openssl rand -base64 32` |
| `CREDS_KEY` | 64-char hex — `openssl rand -hex 32` |
| `CREDS_IV` | 32-char hex — `openssl rand -hex 16` |
| `JWT_SECRET` | Random string |
| `JWT_REFRESH_SECRET` | Random string |

---

## 16. Troubleshooting on Windows

### Docker Desktop won't start

- Ensure Hyper-V and WSL 2 are enabled: run `wsl --status` in PowerShell
- Run `wsl --update` to update the WSL kernel

### `docker compose up` fails on port 10000 (Azurite)

Azurite port 10000 may be in use by IIS or another process:
```powershell
netstat -ano | findstr :10000
# Kill the PID shown or change the Azurite port in docker-compose.yml
```

### PostgreSQL `FATAL: password authentication failed`

The postgres container stores data in a named volume. If you changed the password, delete the volume:
```bash
docker compose down -v
docker compose up -d
```

### Python `poetry install` fails with SSL error

Behind a corporate proxy — set:
```powershell
$env:REQUESTS_CA_BUNDLE = "C:\path\to\corp-ca.crt"
poetry config certificates.default.cert C:\path\to\corp-ca.crt
```

### `terraform init` fails — cannot access tfstate storage

Ensure the Terraform state storage account exists (see §5.1) and that your `az login` session is active:
```bash
az account show
az storage account list --resource-group ve-tfstate-rg
```

### Angular `npm ci` fails — node-gyp errors

Install Windows Build Tools:
```powershell
npm install --global windows-build-tools
# or in newer Node:
npm install --global node-gyp
```

### Service Bus emulator won't start

The emulator requires SQL Server (MSSQL). Check that the `mssql` service is healthy before the emulator starts:
```bash
docker compose logs mssql
# Wait for: "SQL Server is now ready for client connections"
```

The first start of MSSQL can take 30–60 seconds.

### LibreChat iframe blocked by CORS

In local dev, ensure the Angular `devServer` configuration allows cross-origin iframes. The `sandbox` attribute on the iframe must include `allow-scripts allow-same-origin`. This is already configured in `chat.component.ts`.

---

## Quick reference commands

```bash
# Start local stack
cd infrastructure/docker-compose && docker compose up -d

# Stop local stack
docker compose down

# View logs for a service
docker compose logs -f api-gateway

# Rebuild a single service
docker compose up -d --build api-gateway

# Run unit tests
cd backend/api-gateway && npm test
cd backend/agent-orchestrator && poetry run pytest tests/unit/

# Run integration tests
bash scripts/run-integration-local.sh

# Init DB
python scripts/init_db.py

# Deploy dev (Terraform)
cd infrastructure/terraform/envs/dev && terraform apply

# --- External agents ---

# Start LibreChat official (port 3081) + MCP bridge
cd external-agents/librechat && docker compose up -d

# Start MCP bridge only (for Claude Desktop)
bash external-agents/claude-desktop/scripts/start-mcp-bridge.sh
# or: cd infrastructure/docker-compose && docker compose --profile external-agents up mcp-bridge -d

# Check MCP bridge health (expect tools_loaded: 19)
curl http://localhost:8300/health

# Install Claude Desktop config (Windows PowerShell)
.\external-agents\claude-desktop\scripts\install.ps1

# Test stdio bridge (Claude Desktop path)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | \
  docker exec -i video-extract-mcp-bridge python -m app.stdio_entry

# Stop LibreChat stack
cd external-agents/librechat && docker compose down
```
