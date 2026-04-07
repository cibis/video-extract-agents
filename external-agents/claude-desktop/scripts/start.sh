#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Verify the main stack network exists
if ! docker network inspect video-extract-network >/dev/null 2>&1; then
  echo "ERROR: video-extract-network not found."
  echo "Start the main stack first:"
  echo "  cd $(realpath "$SCRIPT_DIR/../../infrastructure/docker-compose")"
  echo "  docker-compose up -d"
  exit 1
fi

docker-compose up -d --build
echo ""
echo "Claude Desktop external agent stack started."
echo "  MCP bridge: http://localhost:8301/health"
echo "  Upload server: docker exec -i video-extract-cd-upload python -m app.main"
echo ""
echo "Install the Claude Desktop config:"
echo "  Windows:  .\\scripts\\install.ps1"
echo "  macOS:    ./scripts/install.sh"
echo ""
echo "Then restart Claude Desktop."
