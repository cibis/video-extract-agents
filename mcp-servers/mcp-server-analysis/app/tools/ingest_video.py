"""ingest_video tool — download, upload, and keyframe-index a video for external agents.

Designed for use by LibreChat and Claude Desktop agents that attach video files
directly via chat rather than going through the normal upload + preprocessing pipeline.

Flow:
  1. Normalise source_url (localhost:10000 → azurite:10000 for container-to-container access)
  2. Download video to a temp file via httpx
  3. Upload original video to Blob Storage  (videos/{session_id}/original/{filename})
  4. Insert row in `videos` table
  5. Run FFmpeg keyframe extraction (mirrors preprocessing-worker/app/processor.py)
  6. Upload each keyframe image to Blob Storage
  7. Insert rows in `video_keyframe_index` table
  8. Write keyframe index JSON blob (list of {frame_index, frame_url, timestamp_seconds})
  9. Optionally insert `session_assets` row (asset_type = 'uploaded_video')
 10. Return video_url, keyframe_index_asset, session_id, video_id, frame_count, duration_seconds
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx
from azure.storage.blob.aio import BlobServiceClient

from app.config import settings
from app.db import get_app_setting, get_pool

logger = logging.getLogger(__name__)

# Local dev user UUID — matches LOCAL_DEV_SKIP_AUTH identity in api-gateway and init_db.py seed
_LOCAL_DEV_USER_ID = "00000000-0000-0000-0000-000000000001"

# ------------------------------------------------------------------
# URL normalisation
# ------------------------------------------------------------------

def _normalise_source_url(url: str) -> str:
    """Replace localhost:10000 with azurite:10000 for container-to-container access.

    The Azure Storage MCP server running on the host machine returns blob URLs with
    localhost:10000 (Azurite's host-facing port).  Inside the Docker network the
    Azurite container is reachable as azurite:10000, so we remap the host reference.
    """
    return re.sub(r"localhost:10000", "azurite:10000", url)


# ------------------------------------------------------------------
# FFmpeg helpers (mirrors preprocessing-worker/app/processor.py)
# ------------------------------------------------------------------

def _build_select_expr(fps: float, scene_threshold: float) -> str:
    interval = 1.0 / fps
    return (
        f"gt(scene,{scene_threshold})"
        f"+isnan(prev_selected_t)"
        f"+gte(t-prev_selected_t,{interval:.4f})"
    )


async def _get_selected_frame_timestamps(
    video_path: str, fps: float, scene_threshold: float
) -> list[float]:
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
    return [
        float(m.group(1))
        for m in re.finditer(r"pts_time:(\d+\.?\d*)", stderr.decode(errors="replace"))
    ]


async def _extract_keyframes(
    video_path: str, output_dir: str, fps: float, scene_threshold: float
) -> list[dict]:
    frames_dir = Path(output_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = str(frames_dir / "frame_%04d.jpg")

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
        timestamps = []
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
            raise RuntimeError(f"FFmpeg keyframe extraction failed: {stderr2.decode()}")

    frame_files = sorted(frames_dir.glob("frame_*.jpg"))
    keyframes = []
    for idx, frame_file in enumerate(frame_files):
        ts = timestamps[idx] if idx < len(timestamps) else idx / fps
        keyframes.append({"frame_index": idx, "local_path": str(frame_file), "timestamp_seconds": ts})
    return keyframes


async def _get_video_duration(video_path: str) -> float:
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


# ------------------------------------------------------------------
# Blob helpers
# ------------------------------------------------------------------

async def _upload_blob(blob_path: str, data: bytes, content_type: str) -> str:
    """Upload bytes to Blob Storage and return the blob URL."""
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        blob_client = client.get_blob_client(
            container=settings.azure_storage_container_name,
            blob=blob_path,
        )
        await blob_client.upload_blob(data, overwrite=True, content_type=content_type)
        return blob_client.url


async def _upload_file(blob_path: str, file_path: str, content_type: str) -> str:
    """Upload a local file to Blob Storage and return the blob URL."""
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        blob_client = client.get_blob_client(
            container=settings.azure_storage_container_name,
            blob=blob_path,
        )
        with open(file_path, "rb") as f:
            await blob_client.upload_blob(f, overwrite=True, content_type=content_type)
        return blob_client.url


# ------------------------------------------------------------------
# DB helpers (write operations not in app.db)
# ------------------------------------------------------------------

async def _ensure_session(session_id: str) -> None:
    """Create the session row if it does not yet exist."""
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO sessions (id, user_id) VALUES ($1::uuid, $2::uuid) ON CONFLICT (id) DO NOTHING""",
        session_id, _LOCAL_DEV_USER_ID,
    )


async def _insert_video(video_id: str, session_id: str | None, blob_url: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO videos (id, user_id, session_id, original_url, status)
           VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'indexed')
           ON CONFLICT (id) DO NOTHING""",
        video_id, _LOCAL_DEV_USER_ID, session_id, blob_url,
    )


async def _insert_keyframes(video_id: str, keyframes: list[dict]) -> None:
    pool = await get_pool()
    await pool.executemany(
        """INSERT INTO video_keyframe_index (video_id, frame_index, frame_url, timestamp_seconds)
           VALUES ($1::uuid, $2, $3, $4)
           ON CONFLICT (video_id, frame_index) DO NOTHING""",
        [(video_id, kf["frame_index"], kf["frame_url"], kf["timestamp_seconds"]) for kf in keyframes],
    )


async def _insert_session_asset(session_id: str, video_id: str, blob_url: str, filename: str) -> None:
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO session_assets (session_id, asset_type, blob_url, filename, content_type, source_id)
           VALUES ($1::uuid, 'uploaded_video', $2, $3, 'video/mp4', $4::uuid)
           ON CONFLICT (session_id, source_id) DO NOTHING""",
        session_id, blob_url, filename, video_id,
    )


# ------------------------------------------------------------------
# Tool entry point
# ------------------------------------------------------------------

async def ingest_video(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Download, upload, and keyframe-index a video from an external source URL.

    Input:
      source_url: str  — HTTP(S) or Azurite blob URL of the video
      job_id: str      — caller-generated UUID used to scope generated assets
      session_id: str  — optional; groups this video with a session
      filename: str    — optional; desired filename in Blob Storage

    Output:
      video_url: str                — blob URL of the original video
      keyframe_index_asset: str     — blob URL of the keyframe index JSON
      session_id: str
      video_id: str
      frame_count: int
      duration_seconds: float
    """
    source_url: str = payload.get("source_url", "")
    job_id: str = payload.get("job_id", "") or str(uuid.uuid4())
    session_id: str | None = payload.get("session_id") or None
    filename: str = payload.get("filename", "") or ""

    if not source_url:
        raise ValueError("source_url is required")

    # Normalise localhost → azurite for container-to-container blob access
    source_url = _normalise_source_url(source_url)

    # Derive filename from URL if not provided
    if not filename:
        filename = source_url.rstrip("/").split("/")[-1].split("?")[0] or "video.mp4"
    if "." not in filename:
        filename += ".mp4"

    logger.info("ingest_video: source=%s job_id=%s session_id=%s", source_url, job_id, session_id)

    # Read keyframe extraction settings from app_settings (same as preprocessing-worker)
    fps_str = await get_app_setting("keyframe_fps")
    scene_str = await get_app_setting("keyframe_scene_threshold")
    fps = float(fps_str) if fps_str else 1.5
    scene_threshold = float(scene_str) if scene_str else 0.2

    # Ensure session exists (external agents generate their own UUIDs)
    if session_id:
        await _ensure_session(session_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, filename)

        # Download video
        logger.info("ingest_video: downloading %s", source_url)
        async with httpx.AsyncClient(timeout=36000.0, follow_redirects=True) as client:
            async with client.stream("GET", source_url) as resp:
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
        logger.info("ingest_video: downloaded %d bytes", os.path.getsize(video_path))

        # Get duration
        duration_seconds = await _get_video_duration(video_path)

        # Upload original video to Blob Storage
        scope = session_id or job_id
        video_blob_path = f"videos/external/{scope}/original/{filename}"
        with open(video_path, "rb") as f:
            video_data = f.read()
        video_url = await _upload_blob(video_blob_path, video_data, "video/mp4")
        logger.info("ingest_video: uploaded original to %s", video_url)

        # Insert video DB record
        video_id = str(uuid.uuid4())
        await _insert_video(video_id, session_id, video_url)

        # Extract keyframes
        logger.info("ingest_video: extracting keyframes fps=%.2f scene_threshold=%.2f", fps, scene_threshold)
        keyframes = await _extract_keyframes(video_path, tmpdir, fps, scene_threshold)
        logger.info("ingest_video: extracted %d keyframes", len(keyframes))

        # Upload keyframe images
        keyframe_records = []
        for kf in keyframes:
            kf_blob_path = (
                f"videos/external/{scope}/keyframes/{video_id}/frame_{kf['frame_index']:04d}.jpg"
            )
            frame_url = await _upload_file(kf_blob_path, kf["local_path"], "image/jpeg")
            keyframe_records.append({
                "frame_index": kf["frame_index"],
                "frame_url": frame_url,
                "timestamp_seconds": kf["timestamp_seconds"],
            })

        # Insert keyframe index into DB
        await _insert_keyframes(video_id, keyframe_records)

        # Write keyframe index JSON blob (list format — compatible with extract_frames)
        import json as _json
        index_json = _json.dumps(keyframe_records, ensure_ascii=False).encode("utf-8")
        index_blob_path = f"videos/external/{scope}/keyframe-index/{video_id}.json"
        keyframe_index_asset = await _upload_blob(index_blob_path, index_json, "application/json")
        logger.info("ingest_video: wrote keyframe index to %s", keyframe_index_asset)

        # Insert session_assets row if session_id provided
        if session_id:
            await _insert_session_asset(session_id, video_id, video_url, filename)

    logger.info(
        "ingest_video: complete video_id=%s frames=%d duration=%.1fs",
        video_id, len(keyframe_records), duration_seconds,
    )
    return {
        "video_url": video_url,
        "keyframe_index_asset": keyframe_index_asset,
        "session_id": session_id or "",
        "video_id": video_id,
        "frame_count": len(keyframe_records),
        "duration_seconds": duration_seconds,
    }
