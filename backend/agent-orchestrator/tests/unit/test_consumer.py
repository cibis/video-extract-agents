import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_process_job_message_success():
    mock_job = {
        "id": "job-1",
        "prompt": "extract jumps",
        "video_url": "http://video.mp4",
    }

    with patch("app.consumer.get_job", new_callable=AsyncMock, return_value=mock_job), \
         patch("app.consumer.update_job_status", new_callable=AsyncMock) as mock_update, \
         patch("app.consumer.run_crew", new_callable=AsyncMock, return_value="http://output.mp4") as mock_run, \
         patch("app.consumer.publish_job_result", new_callable=AsyncMock) as mock_publish:

        from app.consumer import process_job_message
        await process_job_message({
            "jobId": "job-1",
            "userId": "user-1",
            "prompt": "extract jumps",
            "videoUrl": "http://video.mp4",
        })

    mock_run.assert_called_once()
    assert mock_update.call_count == 2  # processing + completed
    mock_publish.assert_called_once()


@pytest.mark.asyncio
async def test_process_job_message_failure():
    mock_job = {
        "id": "job-1",
        "prompt": "extract jumps",
        "video_url": "http://video.mp4",
    }

    with patch("app.consumer.get_job", new_callable=AsyncMock, return_value=mock_job), \
         patch("app.consumer.update_job_status", new_callable=AsyncMock) as mock_update, \
         patch("app.consumer.run_crew", new_callable=AsyncMock, side_effect=Exception("Agent crashed")), \
         patch("app.consumer.publish_job_result", new_callable=AsyncMock) as mock_publish:

        from app.consumer import process_job_message
        await process_job_message({"jobId": "job-1", "userId": "user-1"})

    # Should publish job-failed
    mock_publish.assert_called_once_with("failed", pytest.approx({"job_id": "job-1", "user_id": "user-1", "error": "Agent crashed"}, abs=1e-9))


@pytest.mark.asyncio
async def test_process_job_message_missing_job_id():
    with patch("app.consumer.get_job", new_callable=AsyncMock) as mock_get:
        from app.consumer import process_job_message
        await process_job_message({})  # No jobId

    mock_get.assert_not_called()
