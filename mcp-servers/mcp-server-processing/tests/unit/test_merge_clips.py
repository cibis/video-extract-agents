import pytest
from unittest.mock import AsyncMock, patch, mock_open


@pytest.mark.asyncio
async def test_merge_clips_success():
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("app.tools.merge_clips.asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("app.tools.merge_clips.upload_to_blob", new_callable=AsyncMock, return_value="http://blob/output.mp4"), \
         patch("builtins.open", mock_open()):
        from app.tools.merge_clips import merge_clips
        result = await merge_clips({
            "clip_urls": ["http://clip1.mp4", "http://clip2.mp4"],
            "output_name": "highlight_reel",
        })

    assert result["output_url"] == "http://blob/output.mp4"


@pytest.mark.asyncio
async def test_merge_clips_empty_raises():
    from app.tools.merge_clips import merge_clips
    with pytest.raises(ValueError, match="clip_urls must not be empty"):
        await merge_clips({"clip_urls": []})
