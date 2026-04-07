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

# Verify .env exists
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.example and set ANTHROPIC_API_KEY:"
  echo "  cp .env.example .env"
  exit 1
fi

docker-compose up -d
echo ""
echo "LibreChat (external agent) started."
echo "  URL:       http://localhost:3081"
echo "  MCP bridge: http://localhost:8300/health"
echo ""
echo "Usage:"
echo "  1. Open http://localhost:3081 and create an account."
echo "  2. Start a new chat."
echo "  3. Click the paperclip → attach agent-instructions/video-extraction-agent.md"
echo "  4. Click the paperclip again → attach your video file."
echo "  5. Type your extraction prompt."
