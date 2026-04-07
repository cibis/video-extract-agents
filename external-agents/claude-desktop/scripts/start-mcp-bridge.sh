#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(realpath "$SCRIPT_DIR/../../../infrastructure/docker-compose")"

cd "$COMPOSE_DIR"
docker-compose --profile external-agents up mcp-bridge -d

echo ""
echo "MCP bridge starting at http://localhost:8300"
echo "Health check:"
echo "  curl http://localhost:8300/health"
echo ""
echo "Expected response: {\"status\": \"ok\", \"tools_loaded\": <N>}"
echo "N should be > 0 (typically 18+ tools including ingest_video)."
