import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path


@pytest.mark.asyncio
async def test_extract_keyframes_success(tmp_path):
    # Create a fake frame file to simulate FFmpeg output
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "frame_0001.jpg").touch()
    (frames_dir / "frame_0002.jpg").touch()

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("app.processor.asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.glob", return_value=sorted(frames_dir.glob("frame_*.jpg"))):
        from app.processor import extract_keyframes
        result = await extract_keyframes(str(tmp_path / "video.mp4"), str(tmp_path))

    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_extract_keyframes_falls_back_on_ffmpeg_error(tmp_path):
    # Pre-pass (showinfo) succeeds with no timestamps
    mock_prepass = AsyncMock()
    mock_prepass.returncode = 0
    mock_prepass.communicate = AsyncMock(return_value=(b"", b""))

    # Primary extraction fails
    mock_proc_fail = AsyncMock()
    mock_proc_fail.returncode = 1
    mock_proc_fail.communicate = AsyncMock(return_value=(b"", b"FFmpeg error"))

    # Fallback (fps=N) succeeds
    mock_proc_ok = AsyncMock()
    mock_proc_ok.returncode = 0
    mock_proc_ok.communicate = AsyncMock(return_value=(b"", b""))

    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    with patch("app.processor.asyncio.create_subprocess_exec",
               side_effect=[mock_prepass, mock_proc_fail, mock_proc_ok]), \
         patch("pathlib.Path.glob", return_value=[]):
        from app.processor import extract_keyframes
        result = await extract_keyframes(str(tmp_path / "video.mp4"), str(tmp_path))

    assert result == []


@pytest.mark.asyncio
async def test_get_video_duration():
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"120.5\n", b""))

    with patch("app.processor.asyncio.create_subprocess_exec", return_value=mock_proc):
        from app.processor import get_video_duration
        duration = await get_video_duration("/fake/video.mp4")

    assert duration == pytest.approx(120.5)


@pytest.mark.asyncio
async def test_extract_keyframes_custom_fps_and_threshold(tmp_path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "frame_0001.jpg").touch()

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("app.processor.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec, \
         patch("pathlib.Path.glob", return_value=sorted(frames_dir.glob("frame_*.jpg"))):
        from app.processor import extract_keyframes
        result = await extract_keyframes(
            str(tmp_path / "video.mp4"), str(tmp_path), fps=2.0, scene_threshold=0.1
        )

    # Verify the select expression contains custom fps interval (1/2.0 = 0.5000) and threshold
    all_calls = mock_exec.call_args_list
    primary_call_args = " ".join(all_calls[1][0])  # second call is primary extraction
    assert "0.1" in primary_call_args
    assert "0.5000" in primary_call_args
    assert isinstance(result, list)
