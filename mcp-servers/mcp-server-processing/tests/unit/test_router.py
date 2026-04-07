import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_list_tools():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/tools")
    assert response.status_code == 200
    tools = response.json()
    tool_names = {t["name"] for t in tools}
    assert "split_video" in tool_names
    assert "extract_clip" in tool_names
    assert "merge_clips" in tool_names
    assert "transform_video" in tool_names


@pytest.mark.asyncio
async def test_invoke_unknown_tool():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/tools/nonexistent/invoke", json={})
    assert response.status_code == 404
