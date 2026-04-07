import sys
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_transcribe_audio_raises_on_missing_whisper():
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    sys.modules.pop("app.tools.transcribe_audio", None)
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.path.exists", return_value=True), \
         patch.dict(sys.modules, {"whisper": None}):
        from app.tools.transcribe_audio import transcribe_audio
        with pytest.raises(RuntimeError, match="openai-whisper"):
            await transcribe_audio({"video_url": "http://video.mp4", "language": "en"})
    sys.modules.pop("app.tools.transcribe_audio", None)


@pytest.mark.asyncio
async def test_transcribe_audio_returns_error_on_ffmpeg_failure():
    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"ffmpeg error"))

    sys.modules.pop("app.tools.transcribe_audio", None)
    with patch("app.tools.transcribe_audio.asyncio.create_subprocess_exec", return_value=mock_proc):
        from app.tools.transcribe_audio import transcribe_audio
        result = await transcribe_audio({"video_url": "http://video.mp4"})

    assert result.get("error") == "Audio extraction failed"
    assert result.get("transcript") == ""
    sys.modules.pop("app.tools.transcribe_audio", None)
