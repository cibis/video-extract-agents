"""Unit tests for app.bridge — SSE parsing and error handling."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _sse_lines(*events: dict) -> list[str]:
    """Build a list of SSE data lines from event dicts."""
    return [f"data: {json.dumps(ev)}" for ev in events]


class _FakeStreamResponse:
    """Mock httpx streaming response."""
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    def stream(self, method, url, **kwargs):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_invoke_returns_result():
    lines = _sse_lines(
        {"status": "progress", "message": "working"},
        {"status": "result", "result": {"output_url": "http://blob/file.mp4"}},
    )
    fake_resp = _FakeStreamResponse(lines)
    fake_client = _FakeAsyncClient(fake_resp)

    with patch("app.bridge.httpx.AsyncClient", return_value=fake_client):
        from app.bridge import invoke_custom_sse_tool
        result = await invoke_custom_sse_tool("http://server:8100", "some_tool", {"job_id": "x"})

    assert result == {"output_url": "http://blob/file.mp4"}


@pytest.mark.asyncio
async def test_invoke_raises_on_error():
    lines = _sse_lines({"status": "error", "message": "something went wrong"})
    fake_resp = _FakeStreamResponse(lines)
    fake_client = _FakeAsyncClient(fake_resp)

    with patch("app.bridge.httpx.AsyncClient", return_value=fake_client):
        from app.bridge import invoke_custom_sse_tool
        with pytest.raises(RuntimeError, match="something went wrong"):
            await invoke_custom_sse_tool("http://server:8100", "some_tool", {})


@pytest.mark.asyncio
async def test_invoke_raises_on_validation_error():
    lines = _sse_lines({
        "status": "validation_error",
        "errors": ["field required: job_id"],
        "hint": "Pass job_id in the payload.",
    })
    fake_resp = _FakeStreamResponse(lines)
    fake_client = _FakeAsyncClient(fake_resp)

    with patch("app.bridge.httpx.AsyncClient", return_value=fake_client):
        from app.bridge import invoke_custom_sse_tool
        with pytest.raises(RuntimeError, match="field required"):
            await invoke_custom_sse_tool("http://server:8100", "some_tool", {})


@pytest.mark.asyncio
async def test_invoke_skips_non_data_lines():
    """Lines without 'data: ' prefix are silently ignored."""
    lines = [
        ": keep-alive",
        "",
        f"data: {json.dumps({'status': 'result', 'result': {'ok': True}})}",
    ]
    fake_resp = _FakeStreamResponse(lines)
    fake_client = _FakeAsyncClient(fake_resp)

    with patch("app.bridge.httpx.AsyncClient", return_value=fake_client):
        from app.bridge import invoke_custom_sse_tool
        result = await invoke_custom_sse_tool("http://server:8100", "tool", {})

    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_fetch_catalogue_tags_server_url():
    fake_tools = [{"name": "extract_frames", "description": "desc"}]
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=fake_tools)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.bridge.httpx.AsyncClient", return_value=mock_client):
        from app.bridge import fetch_catalogue
        tools = await fetch_catalogue("http://analysis:8100")

    assert tools[0]["_server_url"] == "http://analysis:8100"
    assert tools[0]["name"] == "extract_frames"
