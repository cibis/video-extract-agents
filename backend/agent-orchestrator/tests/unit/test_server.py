import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def mock_crew():
    with patch("app.server.run_crew", new_callable=AsyncMock) as mock:
        mock.return_value = "http://blob.example.com/output.mp4"
        yield mock


@pytest.fixture
def mock_update_job():
    with patch("app.server.update_job_status", new_callable=AsyncMock) as mock:
        yield mock


@pytest.mark.asyncio
async def test_run_endpoint_success(mock_crew, mock_update_job):
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/run",
            json={
                "prompt": "extract all jumps",
                "video_url": "http://blob.example.com/video.mp4",
                "job_id": "job-123",
                "user_id": "user-456",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["output_url"] == "http://blob.example.com/output.mp4"
    assert data["job_id"] == "job-123"
    assert response.headers.get("x-job-id") == "job-123"


@pytest.mark.asyncio
async def test_run_endpoint_no_job_id(mock_crew, mock_update_job):
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/run",
            json={"prompt": "extract jumps", "video_url": "http://example.com/v.mp4"},
        )
    assert response.status_code == 200
    assert response.json()["output_url"] == "http://blob.example.com/output.mp4"
    # update_job_status should NOT be called when no job_id supplied
    mock_update_job.assert_not_called()


@pytest.mark.asyncio
async def test_health_endpoint():
    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
