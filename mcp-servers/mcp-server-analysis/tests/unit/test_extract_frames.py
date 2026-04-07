import pytest


@pytest.mark.asyncio
async def test_extract_frames_returns_all_when_no_filter():
    from app.tools.extract_frames import extract_frames
    result = await extract_frames({
        "video_url": "http://video.mp4",
        "keyframe_index": [
            {"frame_index": 0, "frame_url": "http://frame0.jpg", "timestamp_seconds": 0.0},
            {"frame_index": 1, "frame_url": "http://frame1.jpg", "timestamp_seconds": 1.0},
        ],
    })
    assert len(result["frames"]) == 2
    assert result["frames"][0]["url"] == "http://frame0.jpg"


@pytest.mark.asyncio
async def test_extract_frames_filters_by_index():
    from app.tools.extract_frames import extract_frames
    result = await extract_frames({
        "video_url": "http://video.mp4",
        "frame_indices": [1],
        "keyframe_index": [
            {"frame_index": 0, "frame_url": "http://frame0.jpg", "timestamp_seconds": 0.0},
            {"frame_index": 1, "frame_url": "http://frame1.jpg", "timestamp_seconds": 1.0},
        ],
    })
    assert len(result["frames"]) == 1
    assert result["frames"][0]["url"] == "http://frame1.jpg"


@pytest.mark.asyncio
async def test_extract_frames_empty_index():
    from app.tools.extract_frames import extract_frames
    result = await extract_frames({"video_url": "http://video.mp4", "keyframe_index": []})
    assert result["frames"] == []
