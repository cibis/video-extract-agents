# Install Claude Desktop MCP config for the Video Extraction Platform.
# Run from PowerShell:
#   external-agents\claude-desktop\scripts\install.ps1

# Claude Desktop (Store app) keeps its config in the package LocalCache, not %APPDATA%
$dest = "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json"
# Fall back to the classic location if the store app path doesn't exist
if (-not (Test-Path (Split-Path $dest))) {
    $dest = "$env:APPDATA\Claude\claude_desktop_config.json"
}
$source = "$PSScriptRoot\..\config\claude_desktop_config.json"

New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
Copy-Item -Path $source -Destination $dest -Force

Write-Host ""
Write-Host "Config installed to: $dest"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Ensure the main stack is running:"
Write-Host "       cd infrastructure\docker-compose"
Write-Host "       docker-compose up -d"
Write-Host ""
Write-Host "  2. Start the Claude Desktop agent stack:"
Write-Host "       cd external-agents\claude-desktop"
Write-Host "       bash scripts\start.sh"
Write-Host ""
Write-Host "  3. Verify:"
Write-Host "       curl http://localhost:8301/health   # MCP bridge (tools)"
Write-Host "       docker ps --filter name=video-extract-cd   # both containers running"
Write-Host ""
Write-Host "  4. Restart Claude Desktop to apply the new config."
Write-Host ""
Write-Host "  5. Verify: the Tools icon should show 'video-extraction-tools' and 'upload-tools'."
