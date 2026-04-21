#!/usr/bin/env bash
# Install Claude Desktop MCP config for the Video Extraction Platform (macOS).
# Run: external-agents/claude-desktop/scripts/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/../config/claude_desktop_config.json"
DEST="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

mkdir -p "$(dirname "$DEST")"
cp "$SOURCE" "$DEST"

echo ""
echo "Config installed to: $DEST"
echo ""
echo "Next steps:"
echo "  1. Ensure the main stack is running:"
echo "       cd infrastructure/docker-compose && docker-compose up -d"
echo ""
echo "  2. Start the Claude Desktop agent stack:"
echo "       cd external-agents/claude-desktop && ./scripts/start.sh"
echo ""
echo "  3. Verify:"
echo "       curl http://localhost:8301/health   # MCP bridge (tools)"
echo "       docker ps --filter name=video-extract-cd   # both containers running"
echo ""
echo "  4. Restart Claude Desktop to apply the new config."
echo ""
echo "  5. Verify: the Tools icon should show 'video-extraction-tools' and 'upload-tools'."
