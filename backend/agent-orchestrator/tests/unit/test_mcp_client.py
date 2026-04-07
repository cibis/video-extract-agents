import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_invoke_mcp_tool_returns_result():
    sse_lines = [
        'data: {"status": "processing", "message": "Starting..."}',
        'data: {"status": "result", "result": {"clip_url": "http://blob/clip.mp4"}}',
        'data: {"status": "done"}',
    ]

    async def mock_aiter_lines():
        for line in sse_lines:
            yield line

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.aiter_lines = mock_aiter_lines

    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tools.mcp_client.httpx.AsyncClient", return_value=mock_client_ctx):
        from app.tools.mcp_client import invoke_mcp_tool
        result = await invoke_mcp_tool(
            "http://mcp-server:8200",
            "extract_clip",
            {"video_url": "http://video.mp4", "start_seconds": 10.0, "end_seconds": 25.0},
        )

    assert result == {"clip_url": "http://blob/clip.mp4"}


@pytest.mark.asyncio
async def test_invoke_mcp_tool_raises_on_error():
    sse_lines = [
        'data: {"status": "error", "message": "FFmpeg failed"}',
    ]

    async def mock_aiter_lines():
        for line in sse_lines:
            yield line

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.aiter_lines = mock_aiter_lines

    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tools.mcp_client.httpx.AsyncClient", return_value=mock_client_ctx):
        from app.tools.mcp_client import invoke_mcp_tool
        with pytest.raises(RuntimeError, match="MCP tool error"):
            await invoke_mcp_tool("http://mcp-server:8200", "extract_clip", {})
