#!/bin/bash
# Run e2e tests against the local Docker Compose stack.
# All tests execute inside Docker — no host-side Python or FFmpeg required.
#
# Usage:
#   ./scripts/run-e2e-local.sh                         # run all e2e tests
#   ./scripts/run-e2e-local.sh -k test_detect_motion   # pass extra pytest args
#   ANTHROPIC_API_KEY=sk-... ./scripts/run-e2e-local.sh  # include frontier tests
#
# Windows: run via Git Bash (bundled with Git for Windows).
# If docker-compose --version fails, replace docker-compose with "docker compose".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/infrastructure/docker-compose/docker-compose.yml"
COMPOSE_CMD="docker-compose -f $COMPOSE_FILE"

echo "==> Starting local stack..."
$COMPOSE_CMD up -d --build

wait_for_health() {
  local name="$1"
  local url="$2"
  local max_attempts=40
  local attempt=0
  echo "==> Waiting for $name at $url..."
  while [ "$attempt" -lt "$max_attempts" ]; do
    if curl -sf "$url" > /dev/null 2>&1; then
      echo "    $name is ready."
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
  echo "ERROR: $name did not become healthy after $((max_attempts * 5))s."
  $COMPOSE_CMD logs "$name" 2>/dev/null || true
  return 1
}

wait_for_health "api-gateway" "http://localhost:8000/health"

echo "==> Running e2e tests inside Docker..."
$COMPOSE_CMD --profile e2e run --rm \
  test-runner \
  pytest tests/e2e/ -v --tb=short --timeout=300 "$@"

echo "==> E2E tests complete."
echo "==> Inspect results: bash scripts/inspect-e2e-results.sh"
