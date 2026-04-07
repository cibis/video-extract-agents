#!/bin/bash
# Run integration tests against the local Docker Compose stack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/infrastructure/docker-compose/docker-compose.yml"

echo "==> Starting local stack..."
docker-compose -f "$COMPOSE_FILE" up -d --build

wait_for_service() {
  local name="$1"
  local url="$2"
  local max_attempts=30
  local attempt=0
  echo "==> Waiting for $name at $url..."
  while [ $attempt -lt $max_attempts ]; do
    if curl -sf "$url" > /dev/null 2>&1; then
      echo "    $name is ready."
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 3
  done
  echo "ERROR: $name did not become ready in time."
  docker-compose -f "$COMPOSE_FILE" logs "$name" 2>/dev/null || true
  return 1
}

wait_for_service "api-gateway"          "http://localhost:8000/health"
wait_for_service "agent-orchestrator"   "http://localhost:8001/health"
wait_for_service "mcp-server-analysis"  "http://localhost:8100/health"
wait_for_service "mcp-server-processing" "http://localhost:8200/health"

echo "==> Initialising database..."
DATABASE_URL="postgresql://postgres:postgres@localhost:5433/videoextract" \
  python "$PROJECT_ROOT/scripts/init_db.py"

echo "==> Running integration tests..."
cd "$PROJECT_ROOT"
pytest tests/integration/ -v --tb=short

echo "==> Skipping e2e tests here — use: scripts/run-e2e-local.sh"

echo "==> All tests complete."
