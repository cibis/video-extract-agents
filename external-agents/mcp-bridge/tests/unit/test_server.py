"""Unit tests for app.server — MCP tool translation and routing."""
import json
import pytest
from unittest.mock import AsyncMock, patch


_FAKE_CATALOGUE = {
    "extract_frames": {
        "name": "extract_frames",
        "description": "Return keyframes from the pre-computed index.",
        "capability_tags": ["frames", "keyframes"],
        "cost_tier": "free",
        "specialization": "general",
        "input_schema": {
            "type": "object",
            "properties": {"video_url": {"type": "string"}, "job_id": {"type": "string"}},
            "required": ["video_url", "job_id"],
        },
        "_server_url": "http://analysis:8100",
    },
    "analyze_scene": {
        "name": "analyze_scene",
        "description": "Semantically describe scenes.",
        "capability_tags": ["vision", "frontier", "scene"],
        "cost_tier": "frontier",
        "specialization": "frontier_vision",
        "input_schema": {"type": "object", "properties": {}},
        "_server_url": "http://analysis:8100",
    },
    "merge_clips": {
        "name": "merge_clips",
        "description": "Merge clips into final output.",
        "capability_tags": ["merge", "video"],
        "cost_tier": "free",
        "specialization": "general",
        "input_schema": {"type": "object", "properties": {}},
        "_server_url": "http://processing:8200",
    },
}


@pytest.mark.asyncio
async def test_list_tools_returns_all():
    with patch("app.server.get_tools_list", return_value=list(_FAKE_CATALOGUE.values())):
        from app.server import list_tools
        tools = await list_tools()
    assert len(tools) == 3
    names = {t.name for t in tools}
    assert names == {"extract_frames", "analyze_scene", "merge_clips"}


@pytest.mark.asyncio
async def test_frontier_tool_description_prefixed():
    with patch("app.server.get_tools_list", return_value=[_FAKE_CATALOGUE["analyze_scene"]]):
        from app.server import list_tools
        tools = await list_tools()
    assert tools[0].description.startswith("[FRONTIER TOOL")


@pytest.mark.asyncio
async def test_free_tool_description_not_prefixed():
    with patch("app.server.get_tools_list", return_value=[_FAKE_CATALOGUE["extract_frames"]]):
        from app.server import list_tools
        tools = await list_tools()
    assert not tools[0].description.startswith("[FRONTIER")


@pytest.mark.asyncio
async def test_capability_tags_appended():
    with patch("app.server.get_tools_list", return_value=[_FAKE_CATALOGUE["extract_frames"]]):
        from app.server import list_tools
        tools = await list_tools()
    assert "frames" in tools[0].description
    assert "keyframes" in tools[0].description


@pytest.mark.asyncio
async def test_call_tool_routes_to_correct_server():
    expected_result = {"result_asset": "http://blob/asset.json"}

    with (
        patch("app.server.get_tool_index", return_value=_FAKE_CATALOGUE),
        patch("app.server.invoke_custom_sse_tool", new_callable=AsyncMock, return_value=expected_result) as mock_invoke,
    ):
        from app.server import call_tool
        content = await call_tool("extract_frames", {"video_url": "http://v", "job_id": "j1"})

    mock_invoke.assert_called_once_with(
        "http://analysis:8100",
        "extract_frames",
        {"video_url": "http://v", "job_id": "j1"},
    )
    assert json.loads(content[0].text) == expected_result


@pytest.mark.asyncio
async def test_call_tool_processing_server():
    with (
        patch("app.server.get_tool_index", return_value=_FAKE_CATALOGUE),
        patch("app.server.invoke_custom_sse_tool", new_callable=AsyncMock, return_value={"output_url": "x"}) as mock_invoke,
    ):
        from app.server import call_tool
        await call_tool("merge_clips", {"job_id": "j1"})

    mock_invoke.assert_called_once_with("http://processing:8200", "merge_clips", {"job_id": "j1"})


@pytest.mark.asyncio
async def test_call_unknown_tool_raises():
    with patch("app.server.get_tool_index", return_value=_FAKE_CATALOGUE):
        from app.server import call_tool
        with pytest.raises(ValueError, match="Unknown tool"):
            await call_tool("nonexistent_tool", {})


@pytest.mark.asyncio
async def test_call_tool_wraps_runtime_error_as_mcp_error():
    with (
        patch("app.server.get_tool_index", return_value=_FAKE_CATALOGUE),
        patch("app.server.invoke_custom_sse_tool", new_callable=AsyncMock, side_effect=RuntimeError("tool failed")),
    ):
        from app.server import call_tool
        from mcp import McpError
        with pytest.raises(McpError):
            await call_tool("extract_frames", {"job_id": "j1"})


@pytest.mark.asyncio
async def test_list_prompts_returns_video_extraction_agent():
    from app.server import list_prompts
    prompts = await list_prompts()
    assert len(prompts) == 1
    assert prompts[0].name == "video-extraction-agent"
    assert prompts[0].description
    assert prompts[0].arguments == []


@pytest.mark.asyncio
async def test_get_prompt_returns_instructions():
    expected_text = "# Video Extraction Agent — System Instructions\n..."
    with patch("app.server._load_prompt", return_value=expected_text):
        from app.server import get_prompt
        result = await get_prompt("video-extraction-agent", None)
    assert result.messages[0].role == "user"
    assert result.messages[0].content.text == expected_text


@pytest.mark.asyncio
async def test_get_prompt_unknown_raises():
    from app.server import get_prompt
    with pytest.raises(ValueError, match="Unknown prompt"):
        await get_prompt("nonexistent-prompt", None)
