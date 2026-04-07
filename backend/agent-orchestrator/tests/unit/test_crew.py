import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_run_crew_returns_output_url():
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "http://blob.example.com/outputs/job-1/output.mp4"

    with patch("app.crew.get_keyframe_index_for_video", new_callable=AsyncMock) as mock_kf, \
         patch("app.crew.Crew") as mock_crew_cls:
        mock_kf.return_value = [{"frame_index": 0, "frame_url": "http://frame.jpg", "timestamp_seconds": 0}]
        mock_crew_instance = MagicMock()
        mock_crew_instance.kickoff.return_value = mock_result
        mock_crew_cls.return_value = mock_crew_instance

        from app.crew import run_crew
        result = await run_crew(
            prompt="extract all jumps",
            video_url="http://blob.example.com/video.mp4",
            job_id="job-1",
            user_id="user-1",
        )

    assert result == "http://blob.example.com/outputs/job-1/output.mp4"
    mock_crew_instance.kickoff.assert_called_once()


@pytest.mark.asyncio
async def test_run_crew_handles_missing_keyframes():
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "http://blob.example.com/output.mp4"

    with patch("app.crew.get_keyframe_index_for_video", new_callable=AsyncMock) as mock_kf, \
         patch("app.crew.Crew") as mock_crew_cls:
        mock_kf.side_effect = Exception("DB error")
        mock_crew_instance = MagicMock()
        mock_crew_instance.kickoff.return_value = mock_result
        mock_crew_cls.return_value = mock_crew_instance

        from app.crew import run_crew
        result = await run_crew(
            prompt="extract jumps",
            video_url="http://example.com/video.mp4",
            job_id="job-2",
            user_id="user-2",
        )

    assert result == "http://blob.example.com/output.mp4"
