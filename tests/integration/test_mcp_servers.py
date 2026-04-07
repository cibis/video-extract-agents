"""Integration tests for MCP tool servers via SSE."""
import json
import pytest
import httpx


def parse_sse_result(content: str) -> dict:
    """Parse SSE stream content and return the result event payload."""
    for line in content.splitlines():
        if line.startswith("data: "):
            try:
                event = json.loads(line[6:])
                if event.get("status") == "result":
                    return event.get("result", {})
            except json.JSONDecodeError:
                continue
    return {}


def test_analysis_server_health(mcp_analysis_url, http_client):
    response = http_client.get(f"{mcp_analysis_url}/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_analysis_tool_catalogue(mcp_analysis_url, http_client):
    response = http_client.get(f"{mcp_analysis_url}/tools")
    assert response.status_code == 200
    tools = response.json()
    names = {t["name"] for t in tools}
    assert {"extract_frames", "detect_motion", "detect_objects", "transcribe_audio"}.issubset(names)


def test_extract_frames_tool(mcp_analysis_url, http_client):
    response = http_client.post(
        f"{mcp_analysis_url}/tools/extract_frames/invoke",
        json={
            "video_url": "http://azurite:10000/devstoreaccount1/videos/test.mp4",
            "keyframe_index": [
                {"frame_index": 0, "frame_url": "http://frame0.jpg", "timestamp_seconds": 0.0},
                {"frame_index": 1, "frame_url": "http://frame1.jpg", "timestamp_seconds": 1.0},
            ],
        },
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    result = parse_sse_result(response.text)
    assert "frames" in result
    assert len(result["frames"]) == 2


def test_processing_server_health(mcp_processing_url, http_client):
    response = http_client.get(f"{mcp_processing_url}/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_processing_tool_catalogue(mcp_processing_url, http_client):
    response = http_client.get(f"{mcp_processing_url}/tools")
    assert response.status_code == 200
    tools = response.json()
    names = {t["name"] for t in tools}
    assert {"split_video", "extract_clip", "merge_clips", "transform_video"} == names


def test_invoke_nonexistent_tool(mcp_analysis_url, http_client):
    response = http_client.post(
        f"{mcp_analysis_url}/tools/does_not_exist/invoke",
        json={"video_url": "http://test.mp4"},
    )
    assert response.status_code == 404
