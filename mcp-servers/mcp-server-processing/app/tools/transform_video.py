"""transform_video tool — resize, speed change, color grade."""
import asyncio
import os
import tempfile
import uuid
from typing import Any
from app.tools.blob_helper import upload_to_blob, make_blob_path, get_ffmpeg_accessible_url


def _build_filter_chain(operations: list[dict]) -> str:
    filters = []
    for op in operations:
        op_type = op.get("type")
        if op_type == "resize":
            w = op.get("width", 1280)
            h = op.get("height", 720)
            filters.append(f"scale={w}:{h}")
        elif op_type == "speed":
            factor = float(op.get("factor", 1.0))
            # setpts adjusts video speed; atempo adjusts audio
            pts_factor = 1.0 / factor
            filters.append(f"setpts={pts_factor:.4f}*PTS")
        elif op_type == "color_grade":
            brightness = float(op.get("brightness", 0))
            contrast = float(op.get("contrast", 1))
            filters.append(f"eq=brightness={brightness}:contrast={contrast}")
    return ",".join(filters) if filters else "copy"


async def transform_video(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Apply transformations to a video.

    Input:
      video_url: str
      operations: list[{type, ...params}]
      job_id: str (optional)
      output_name: str (optional)

    Output:
      output_url: str
    """
    video_url = get_ffmpeg_accessible_url(payload["video_url"])
    operations: list[dict] = payload.get("operations", [])
    job_id: str | None = payload.get("job_id") or None
    session_id: str | None = payload.get("session_id") or None
    output_name = payload.get("output_name", f"transformed_{uuid.uuid4().hex[:8]}")

    filter_chain = _build_filter_chain(operations)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, f"{output_name}.mp4")

        cmd = [
            "ffmpeg", "-i", video_url,
            "-vf", filter_chain,
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            output_path, "-y",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg transform_video failed: {stderr.decode()}")

        blob_path = make_blob_path("transformed", output_name, job_id=job_id, session_id=session_id)
        output_url = await upload_to_blob(output_path, blob_path)

    return {"output_url": output_url}
