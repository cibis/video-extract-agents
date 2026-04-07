import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_extract_clip_success():
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("app.tools.extract_clip.asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("app.tools.extract_clip.upload_to_blob", new_callable=AsyncMock, return_value="http://blob/clip.mp4"), \
         patch("os.path.exists", return_value=True), \
         patch("builtins.open", create=True):
        from app.tools.extract_clip import extract_clip
        result = await extract_clip({
            "video_url": "http://video.mp4",
            "start_seconds": 10.0,
            "end_seconds": 25.0,
            "output_name": "clip_001",
        })

    assert result["clip_url"] == "http://blob/clip.mp4"


@pytest.mark.asyncio
async def test_extract_clip_raises_on_ffmpeg_error():
    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"FFmpeg error"))

    with patch("app.tools.extract_clip.asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("builtins.open", create=True):
        from app.tools.extract_clip import extract_clip
        with pytest.raises(RuntimeError, match="FFmpeg extract_clip failed"):
            await extract_clip({
                "video_url": "http://video.mp4",
                "start_seconds": 0,
                "end_seconds": 10,
            })
