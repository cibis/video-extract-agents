"""
Synthetic test video generators using FFmpeg lavfi virtual input sources.

No video files are committed to source control — all videos are generated
at runtime in pytest tmp_path directories and discarded after tests complete.

Each generator targets ~40–80 KB output (low resolution, high compression,
short duration) to keep test execution fast.
"""
import subprocess


def _run_ffmpeg(*args: str) -> None:
    """Run an FFmpeg command; raise RuntimeError on non-zero exit."""
    cmd = ["ffmpeg", "-y", *args]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed (exit {result.returncode}):\n"
            f"{result.stderr.decode(errors='replace')}"
        )


def make_motion_video(path: str) -> None:
    """
    8-second animated test card (testsrc) at 320×180, 5 fps.
    Moving edges and colour bars produce strong optical flow — reliably
    triggers detect_motion.
    """
    _run_ffmpeg(
        "-f", "lavfi",
        "-i", "testsrc=duration=8:size=320x180:rate=5",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-crf", "40",
        "-an",
        path,
    )


def make_sports_video(path: str) -> None:
    """
    8-second high-contrast animated pattern (testsrc2) at 320×180, 5 fps.
    Faster-moving high-contrast pattern produces bursts of intense optical
    flow — triggers detect_motion_sports event detection.
    """
    _run_ffmpeg(
        "-f", "lavfi",
        "-i", "testsrc2=duration=8:size=320x180:rate=5",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-crf", "40",
        "-an",
        path,
    )


def make_static_video(path: str) -> None:
    """
    8-second solid blue field at 320×180, 2 fps.
    Near-zero optical flow — used for scene analysis and vision-model tests
    where motion is not the target.
    """
    _run_ffmpeg(
        "-f", "lavfi",
        "-i", "color=c=blue:duration=8:size=320x180:rate=2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-crf", "45",
        "-an",
        path,
    )


def make_object_video(path: str) -> None:
    """
    8-second animated test card (testsrc) at 320×180, 5 fps.
    Abstract moving pattern — YOLO will likely return 0 detections for
    standard COCO classes. Used for detect_objects pipeline validation
    (verifies the tool runs end-to-end; accepts no_matching_segments result).
    """
    _run_ffmpeg(
        "-f", "lavfi",
        "-i", "testsrc=duration=8:size=320x180:rate=5",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-crf", "40",
        "-an",
        path,
    )


def make_pov_video(path: str) -> None:
    """
    6-second animated test pattern at 320×180, 2 fps.

    Used as a stand-in for first-person (POV/drone) footage in the
    estimate_height_above_surface pipeline test.  Depth Anything V2 Metric
    runs on any image and produces absolute depth values in metres regardless
    of scene content, so visual realism is not required — the test validates
    the pipeline runs end-to-end and the tool is invoked correctly.
    """
    _run_ffmpeg(
        "-f", "lavfi",
        "-i", "testsrc=duration=6:size=320x180:rate=2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-crf", "45",
        "-an",
        path,
    )


def make_audio_video(path: str) -> None:
    """
    10-second video with a 440 Hz sine tone audio track.
    Black video frames (minimal size) with continuous audio — triggers the
    transcribe_audio pipeline. Whisper will produce a sparse/empty transcript
    from a pure tone, but the pipeline still completes successfully.
    """
    _run_ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:duration=10:size=160x90:rate=1",
        "-f", "lavfi", "-i", "sine=f=440:duration=10",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-crf", "51",
        "-c:a", "aac",
        "-b:a", "32k",
        "-shortest",
        path,
    )
