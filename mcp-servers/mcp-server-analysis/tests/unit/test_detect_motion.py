import sys
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_detect_motion_returns_score():
    mock_proc = AsyncMock()
    mock_proc.returncode = 1  # FFmpeg fails → segment file not created → returns 0.0, []
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

    with patch("app.tools.detect_motion.asyncio.create_subprocess_exec", return_value=mock_proc):
        from app.tools.detect_motion import detect_motion
        result = await detect_motion({
            "video_url": "http://video.mp4",
            "segment_start_seconds": 0,
            "segment_end_seconds": 10,
        })

    assert "motion_score" in result
    assert "high_motion_segments" in result
    assert 0.0 <= result["motion_score"] <= 1.0


@pytest.mark.asyncio
async def test_detect_motion_raises_on_missing_opencv():
    sys.modules.pop("app.tools.detect_motion", None)
    with patch.dict(sys.modules, {"cv2": None, "numpy": None}):
        from app.tools.detect_motion import detect_motion
        with pytest.raises(RuntimeError, match="opencv-python"):
            await detect_motion({
                "video_url": "http://video.mp4",
                "segment_start_seconds": 0,
                "segment_end_seconds": 10,
            })
    sys.modules.pop("app.tools.detect_motion", None)
