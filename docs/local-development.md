# Running the Platform Locally

This guide covers day-to-day local development: starting the stack, running individual services, running tests, and common tasks. For first-time machine setup (Docker, Node, Python, Azure accounts, Terraform) see [SETUP.md](../SETUP.md).

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [First-time env setup](#first-time-env-setup)
- [Starting the full local stack](#starting-the-full-local-stack)
  - [Service endpoints](#service-endpoints)
  - [Local dev flags active in Docker Compose](#local-dev-flags-active-in-docker-compose)
- [Initialising the database (first run only)](#initialising-the-database-first-run-only)
- [Creating Service Bus queues (first run only)](#creating-service-bus-queues-first-run-only)
- [Verifying the stack is healthy](#verifying-the-stack-is-healthy)
- [Stopping the stack](#stopping-the-stack)
- [Working with individual services](#working-with-individual-services)
  - [Rebuild and restart a single service](#rebuild-and-restart-a-single-service)
  - [View logs](#view-logs)
  - [Open a shell in a running container](#open-a-shell-in-a-running-container)
- [Running unit tests](#running-unit-tests)
  - [API Gateway (Node.js)](#api-gateway-nodejs)
  - [Agent Orchestrator (Python)](#agent-orchestrator-python)
  - [Preprocessing Worker (Python)](#preprocessing-worker-python)
  - [Notification Worker (Python)](#notification-worker-python)
  - [MCP Server Analysis (Python)](#mcp-server-analysis-python)
  - [MCP Server Processing (Python)](#mcp-server-processing-python)
  - [Run all Python unit tests from repo root](#run-all-python-unit-tests-from-repo-root)
- [Running integration tests](#running-integration-tests)
  - [Using the convenience script](#using-the-convenience-script)
  - [Manually](#manually)
- [Running a service outside Docker (hot reload)](#running-a-service-outside-docker-hot-reload)
  - [API Gateway](#api-gateway)
  - [Python services (agent-orchestrator, workers, MCP servers)](#python-services-agent-orchestrator-workers-mcp-servers)
- [Linting](#linting)
  - [API Gateway](#api-gateway-1)
  - [Python services](#python-services)
- [Database access](#database-access)
- [Azurite (Blob Storage)](#azurite-blob-storage)
- [Common troubleshooting](#common-troubleshooting)
  - [Port already in use](#port-already-in-use)
  - [PostgreSQL "password authentication failed"](#postgresql-password-authentication-failed)
  - [Service Bus emulator slow to start](#service-bus-emulator-slow-to-start)
  - [Agent orchestrator fails with missing ANTHROPIC\_API\_KEY](#agent-orchestrator-fails-with-missing-anthropic_api_key)
  - [`mcp-server-analysis` is slow on the first `detect_objects` call](#mcp-server-analysis-is-slow-on-the-first-detect_objects-call)
  - [`poetry install` fails with SSL errors (corporate proxy)](#poetry-install-fails-with-ssl-errors-corporate-proxy)
- [Quick reference](#quick-reference)

---

## Prerequisites

- Docker Desktop running (WSL 2 engine)
- `.env` files copied from `.env.example` for every service (one-time, see [First-time env setup](#first-time-env-setup))
- `ANTHROPIC_API_KEY` set in `backend/agent-orchestrator/.env`

---

## First-time env setup

Copy all `.env.example` files to `.env` (run once from the repo root):

```bash
cp backend/api-gateway/.env.example               backend/api-gateway/.env
cp backend/agent-orchestrator/.env.example        backend/agent-orchestrator/.env
cp backend/preprocessing-worker/.env.example      backend/preprocessing-worker/.env
cp backend/notification-worker/.env.example       backend/notification-worker/.env
cp mcp-servers/mcp-server-analysis/.env.example   mcp-servers/mcp-server-analysis/.env
cp mcp-servers/mcp-server-processing/.env.example mcp-servers/mcp-server-processing/.env
cp infrastructure/docker-compose/.env.example     infrastructure/docker-compose/.env
```

Then open `backend/agent-orchestrator/.env` and set your real Anthropic key:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

All other values in the `.env.example` files are pre-configured for the local Docker Compose stack and do not need to change.

---

## Starting the full local stack

```bash
cd infrastructure/docker-compose
docker compose up --build
```

Add `-d` to run in the background:

```bash
docker compose up -d --build
```

All services build and start in dependency order. First build takes several minutes; subsequent starts are fast.

### Service endpoints

| Service | URL | Notes |
|---|---|---|
| Angular Shell | http://localhost:4200 | Main UI |
| LibreChat | http://localhost:3080 | Chat interface (also embedded in Angular) |
| API Gateway | http://localhost:8000 | Node.js REST + SSE |
| Agent Orchestrator | http://localhost:8001 | Python CrewAI service |
| MCP Analysis Server | http://localhost:8100 | SSE tool server |
| MCP Processing Server | http://localhost:8200 | SSE tool server |
| Azurite (Blob Storage) | http://localhost:10000 | Azure Blob emulator |
| PostgreSQL | localhost:5433 | `postgres/postgres` |
| Service Bus emulator | localhost:5672 / 5300 | AMQP / management |

### Local dev flags active in Docker Compose

| Flag | Value | Effect |
|---|---|---|
| `LOCAL_DEV_SKIP_AUTH` | `true` | JWT validation bypassed; identity injected as `{ id: "local-dev-user", email: "dev@local" }` |
| `OUTPUT_URL_MODE` | `local` | Output URLs point to Azurite instead of Azure Front Door |
| `NOTIFICATION_MODE` | `stdout` | Notification worker logs emails to stdout instead of sending via ACS |

---

## Initialising the database (first run only)

After the PostgreSQL container is healthy, create all tables:

```bash
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5433/videoextract" \
  python scripts/init_db.py
```

---

## Creating Service Bus queues (first run only)

```bash
AZURE_SERVICE_BUS_CONNECTION_STRING="Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OTeyNNaKQnQ==;UseDevelopmentEmulator=true;" \
  python scripts/create_service_bus_queues.py
```

---

## Verifying the stack is healthy

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"api-gateway"}

curl http://localhost:8001/health
# {"status":"ok","service":"agent-orchestrator"}

curl http://localhost:8100/health
# {"status":"ok","service":"mcp-server-analysis"}

curl http://localhost:8200/health
# {"status":"ok","service":"mcp-server-processing"}

curl http://localhost:8100/tools
# [{"name":"extract_frames",...},{"name":"detect_motion",...},...]
```

---

## Stopping the stack

```bash
# Stop, keep volumes
docker compose down

# Stop and delete all data (DB, Azurite)
docker compose down -v
```

---

## Working with individual services

### Rebuild and restart a single service

```bash
docker compose up -d --build api-gateway
docker compose up -d --build agent-orchestrator
docker compose up -d --build mcp-server-analysis
docker compose up -d --build mcp-server-processing
```

### View logs

```bash
# Follow logs for one service
docker compose logs -f api-gateway
docker compose logs -f agent-orchestrator
docker compose logs -f preprocessing-worker
docker compose logs -f notification-worker

# Follow logs for all services
docker compose logs -f
```

### Open a shell in a running container

```bash
docker compose exec api-gateway sh
docker compose exec agent-orchestrator bash
```

---

## Running unit tests

Unit tests run outside Docker against mocked dependencies. Install local dependencies first.

### API Gateway (Node.js)

```bash
cd backend/api-gateway
npm ci
npm test
```

With verbose output and coverage report:

```bash
npm test -- --verbose --coverage
```

### Agent Orchestrator (Python)

```bash
cd backend/agent-orchestrator
poetry install
poetry run pytest tests/unit/ -v
```

### Preprocessing Worker (Python)

```bash
cd backend/preprocessing-worker
poetry install
poetry run pytest tests/unit/ -v
```

### Notification Worker (Python)

```bash
cd backend/notification-worker
poetry install
poetry run pytest tests/unit/ -v
```

### MCP Server Analysis (Python)

```bash
cd mcp-servers/mcp-server-analysis
poetry install
poetry run pytest tests/unit/ -v
```

### MCP Server Processing (Python)

```bash
cd mcp-servers/mcp-server-processing
poetry install
poetry run pytest tests/unit/ -v
```

### Run all Python unit tests from repo root

```bash
for dir in backend/agent-orchestrator backend/preprocessing-worker backend/notification-worker \
            mcp-servers/mcp-server-analysis mcp-servers/mcp-server-processing; do
  echo "==> $dir"
  (cd "$dir" && poetry install --quiet && poetry run pytest tests/unit/ -v)
done
```

---

## Running integration tests

Integration tests require the full local stack to be running.

### Using the convenience script

```bash
bash scripts/run-integration-local.sh
```

This script:
1. Starts the Docker Compose stack
2. Waits for all services to be healthy
3. Initialises the database
4. Runs `pytest tests/integration/`

### Manually

```bash
# Start the stack
cd infrastructure/docker-compose
docker compose up -d --build

# Wait for health checks to pass, then from repo root:
cd ../..
pip install pytest httpx pytest-asyncio
pytest tests/integration/ -v --tb=short
```

---

## Running a service outside Docker (hot reload)

Useful when iterating quickly on a single service while the rest of the stack runs in Docker.

### API Gateway

```bash
cd backend/api-gateway
npm ci
# Copy and edit .env to point to Docker-hosted dependencies:
#   DATABASE_URL=postgresql://postgres:postgres@localhost:5433/videoextract
#   AGENT_ORCHESTRATOR_URL=http://localhost:8001
npm run dev
```

`npm run dev` uses `ts-node-dev` with `--respawn` for hot reload on file changes.

### Python services (agent-orchestrator, workers, MCP servers)

```bash
cd backend/agent-orchestrator
poetry install
# Ensure .env points to Docker-hosted dependencies (localhost addresses)
poetry run uvicorn app.main:app --reload --port 8001
```

Replace `app.main:app` and the port as appropriate per service:

| Service | Module | Port |
|---|---|---|
| agent-orchestrator | `app.main:app` | 8001 |
| mcp-server-analysis | `app.main:app` | 8100 |
| mcp-server-processing | `app.main:app` | 8200 |

Workers (`preprocessing-worker`, `notification-worker`) are Service Bus-triggered and run as long-lived consumers:

```bash
cd backend/preprocessing-worker
poetry run python -m app.main
```

---

## Linting

### API Gateway

```bash
cd backend/api-gateway
npm run lint
```

### Python services

```bash
cd backend/agent-orchestrator   # or any Python service dir
poetry run ruff check app/ tests/
```

---

## Database access

Connect to the local PostgreSQL with any client using:

```
Host:     localhost
Port:     5433
Database: videoextract
User:     postgres
Password: postgres
```

Or via `psql`:

```bash
docker compose exec postgresql psql -U postgres -d videoextract
```

Useful queries:

```sql
-- Check jobs table
SELECT id, status, created_at FROM jobs ORDER BY created_at DESC LIMIT 10;

-- Check videos table
SELECT id, user_id, blob_url, created_at FROM videos ORDER BY created_at DESC LIMIT 10;
```

---

## Azurite (Blob Storage)

The Azurite emulator is pre-configured with a fixed account:

| Setting | Value |
|---|---|
| Account name | `devstoreaccount1` |
| Account key | `Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OTeyNNaKQnQ==` |
| Blob endpoint | `http://localhost:10000/devstoreaccount1` |

Manage blobs with Azure Storage Explorer (connect using the Azurite connection string) or via the Azure CLI:

```bash
az storage container list \
  --connection-string "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OTeyNNaKQnQ==;BlobEndpoint=http://localhost:10000/devstoreaccount1;"
```

---

## Common troubleshooting

### Port already in use

```bash
# Find what is on port 8000 (Windows)
netstat -ano | findstr :8000

# Find what is on port 10000 (Azurite)
netstat -ano | findstr :10000
```

Kill the process or change the port mapping in `infrastructure/docker-compose/docker-compose.yml`.

### PostgreSQL "password authentication failed"

The postgres volume may contain stale credentials. Reset it:

```bash
docker compose down -v
docker compose up -d
```

Then re-run `scripts/init_db.py`.

### Service Bus emulator slow to start

The emulator depends on an internal MSSQL instance. First start can take 30–60 seconds. If services fail to connect, check:

```bash
docker compose logs servicebus-emulator
```

Wait for the emulator to log that it is ready before starting services that depend on it, or increase the `retries` on the healthcheck in `docker-compose.yml`.

### Agent orchestrator fails with missing ANTHROPIC_API_KEY

Ensure `backend/agent-orchestrator/.env` contains a valid key:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

Then rebuild the container:

```bash
docker compose up -d --build agent-orchestrator
```

### `mcp-server-analysis` is slow on the first `detect_objects` call

`detect_objects` uses YOLOv8n via the `ultralytics` package. On the first call the model weights (`yolov8n.pt`, ~6 MB) are downloaded from Ultralytics servers and cached at `~/.config/Ultralytics/` (inside the container) or the path set by `YOLO_CONFIG_DIR`. Subsequent calls use the cached weights and are fast.

To avoid the download delay at runtime, pre-cache the model during the Docker build by adding the following line to `mcp-servers/mcp-server-analysis/Dockerfile` after `poetry install`:

```dockerfile
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
```

This requires internet access at build time. In environments where the build host has no outbound access, copy `yolov8n.pt` into the image and set `YOLO_CONFIG_DIR` to its directory.

### `poetry install` fails with SSL errors (corporate proxy)

```powershell
$env:REQUESTS_CA_BUNDLE = "C:\path\to\corp-ca.crt"
poetry config certificates.default.cert C:\path\to\corp-ca.crt
```

---

## Quick reference

```bash
# Start stack (background)
cd infrastructure/docker-compose && docker compose up -d --build

# Stop stack
docker compose down

# Tail logs
docker compose logs -f <service-name>

# Rebuild one service
docker compose up -d --build <service-name>

# API Gateway unit tests
cd backend/api-gateway && npm test

# Python unit tests (any service)
cd backend/agent-orchestrator && poetry run pytest tests/unit/ -v

# Integration tests
bash scripts/run-integration-local.sh

# Init DB
DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5433/videoextract" python scripts/init_db.py

# Create Service Bus queues
AZURE_SERVICE_BUS_CONNECTION_STRING="..." python scripts/create_service_bus_queues.py

# Open DB shell
docker compose exec postgresql psql -U postgres -d videoextract
```
