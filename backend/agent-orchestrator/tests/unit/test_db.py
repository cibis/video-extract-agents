import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_get_job_returns_dict():
    mock_row = {"id": "job-1", "status": "queued", "prompt": "test"}
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=mock_row)

    with patch("app.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):
        from app.db import get_job
        result = await get_job("job-1")

    assert result["id"] == "job-1"
    mock_pool.fetchrow.assert_called_once_with("SELECT * FROM jobs WHERE id = $1", "job-1")


@pytest.mark.asyncio
async def test_get_job_returns_none_when_not_found():
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)

    with patch("app.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):
        from app.db import get_job
        result = await get_job("nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_update_job_status():
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()

    with patch("app.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):
        from app.db import update_job_status
        await update_job_status("job-1", "completed", output_url="http://output.mp4")

    mock_pool.execute.assert_called_once()
    call_args = mock_pool.execute.call_args[0]
    assert "completed" in call_args
    assert "http://output.mp4" in call_args


@pytest.mark.asyncio
async def test_get_keyframe_index():
    mock_rows = [
        {"frame_index": 0, "frame_url": "http://frame0.jpg", "timestamp_seconds": 0.0},
        {"frame_index": 1, "frame_url": "http://frame1.jpg", "timestamp_seconds": 1.0},
    ]
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=mock_rows)

    with patch("app.db.get_pool", new_callable=AsyncMock, return_value=mock_pool):
        from app.db import get_keyframe_index
        result = await get_keyframe_index("http://video.mp4")

    assert len(result) == 2
    assert result[0]["frame_index"] == 0
