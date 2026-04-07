import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_process_video_message_success():
    keyframes = [
        {"frame_index": 0, "local_path": "/tmp/frame_0000.jpg", "timestamp_seconds": 0.0},
        {"frame_index": 1, "local_path": "/tmp/frame_0001.jpg", "timestamp_seconds": 0.667},
    ]

    with patch("app.consumer.get_app_setting", new_callable=AsyncMock, return_value="1.5"), \
         patch("app.consumer.download_video", new_callable=AsyncMock), \
         patch("app.consumer.extract_keyframes", new_callable=AsyncMock, return_value=keyframes), \
         patch("app.consumer.upload_keyframe", new_callable=AsyncMock, return_value="http://frame.jpg"), \
         patch("app.consumer.store_keyframe_index", new_callable=AsyncMock), \
         patch("app.consumer.update_video_status", new_callable=AsyncMock) as mock_status, \
         patch("app.consumer.publish_video_indexed", new_callable=AsyncMock) as mock_pub:

        from app.consumer import process_video_message
        await process_video_message({
            "videoId": "video-1",
            "userId": "user-1",
            "blobUrl": "http://blob.example.com/video.mp4",
        })

    mock_status.assert_called_with("video-1", "indexed")
    mock_pub.assert_called_once()


@pytest.mark.asyncio
async def test_process_video_message_invalid():
    with patch("app.consumer.download_video", new_callable=AsyncMock) as mock_dl:
        from app.consumer import process_video_message
        await process_video_message({})  # Missing videoId + blobUrl

    mock_dl.assert_not_called()


@pytest.mark.asyncio
async def test_process_video_message_marks_failed_on_error():
    with patch("app.consumer.get_app_setting", new_callable=AsyncMock, return_value="1.5"), \
         patch("app.consumer.download_video", new_callable=AsyncMock, side_effect=Exception("Download failed")), \
         patch("app.consumer.update_video_status", new_callable=AsyncMock) as mock_status:
        from app.consumer import process_video_message
        with pytest.raises(Exception):
            await process_video_message({
                "videoId": "video-1",
                "userId": "user-1",
                "blobUrl": "http://blob.example.com/video.mp4",
            })

    mock_status.assert_called_with("video-1", "failed")
