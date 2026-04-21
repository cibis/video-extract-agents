"""FFmpeg + OpenCV keyframe extraction pipeline."""
import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_select_expr(fps: float, scene_threshold: float) -> str:
    """
    Build the FFmpeg select filter expression for combined periodic + scene-change extraction.

    Selects a frame when ANY of these is true:
      - No frame has been selected yet (first frame): isnan(prev_selected_t)
      - Enough time has elapsed since last selected frame: gte(t-prev_selected_t, interval)
      - A scene change exceeds the threshold: gt(scene, scene_threshold)
    """
    interval = 1.0 / fps
    return (
        f"gt(scene,{scene_threshold})"
        f"+isnan(prev_selected_t)"
        f"+gte(t-prev_selected_t,{interval:.4f})"
    )


async def _get_selected_frame_timestamps(
    video_path: str,
    fps: float,
    scene_threshold: float,
) -> list[float]:
    """
    Return the precise PTS (presentation timestamp) of every frame that would
    be selected by the periodic + scene-change filter, in selection order.

    Uses the showinfo filter to capture pts_time values from FFmpeg stderr
    without writing any output files.
    """
    select_expr = _build_select_expr(fps, scene_threshold)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"select='{select_expr}',showinfo",
        "-vsync", "vfr",
        "-f", "null", "-",
        "-y",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    stderr_text = stderr.decode(errors="replace")
    return [
        float(m.group(1))
        for m in re.finditer(r"pts_time:(\d+\.?\d*)", stderr_text)
    ]


async def extract_keyframes(
    video_path: str,
    output_dir: str,
    fps: float = 1.5,
    scene_threshold: float = 0.2,
) -> list[dict]:
    """
    Extract keyframes from a video file using FFmpeg.

    Selects frames periodically at `fps` frames/second AND on scene changes
    exceeding `scene_threshold` (0–1; lower = more sensitive).

    Returns list of {frame_index, local_path, timestamp_seconds}.
    """
    frames_dir = Path(output_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = str(frames_dir / "frame_%04d.jpg")

    # Collect precise per-frame timestamps before extraction.
    # Falls back to an empty list; timestamps are then derived from index / fps.
    timestamps = await _get_selected_frame_timestamps(video_path, fps, scene_threshold)

    select_expr = _build_select_expr(fps, scene_threshold)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"select='{select_expr}',setpts=N/FR/TB",
        "-vsync", "vfr",
        "-q:v", "2",
        output_pattern,
        "-y",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        timestamps = []  # showinfo timestamps don't apply to the fallback filter
        cmd_fallback = [
            "ffmpeg", "-i", video_path,
            "-vf", f"fps={fps}",
            "-q:v", "2",
            output_pattern,
            "-y",
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *cmd_fallback,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr2 = await proc2.communicate()
        if proc2.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {stderr2.decode()}")

    frame_files = sorted(frames_dir.glob("frame_*.jpg"))
    keyframes = []
    for idx, frame_file in enumerate(frame_files):
        if idx < len(timestamps):
            # Precise PTS from the showinfo pre-pass
            ts = timestamps[idx]
        else:
            # Fallback: index * interval gives the approximate capture time
            ts = idx / fps
        keyframes.append({
            "frame_index": idx,
            "local_path": str(frame_file),
            "timestamp_seconds": ts,
        })

    return keyframes


async def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        video_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0
