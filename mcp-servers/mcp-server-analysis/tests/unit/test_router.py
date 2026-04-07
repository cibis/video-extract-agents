import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_list_tools():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/tools")
    assert response.status_code == 200
    tools = response.json()
    assert isinstance(tools, list)
    tool_names = {t["name"] for t in tools}
    assert "extract_frames" in tool_names
    assert "detect_motion" in tool_names
    assert "detect_objects" in tool_names
    assert "transcribe_audio" in tool_names


@pytest.mark.asyncio
async def test_invoke_unknown_tool():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/tools/nonexistent/invoke", json={})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_invoke_extract_frames_sse():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/tools/extract_frames/invoke",
            json={
                "video_url": "http://blob.example.com/video.mp4",
                "keyframe_index": [
                    {"frame_index": 0, "frame_url": "http://frame0.jpg", "timestamp_seconds": 0.0}
                ],
            },
        )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    content = response.text
    assert "result" in content
