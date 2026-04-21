"""FastAPI router — POST /upload

Accepts a multipart/form-data upload, writes the file to Azurite (or Azure Blob),
records session/asset rows in PostgreSQL (same schema as the application UI), and
returns the blob URL.

Blob path: videos/external/{session_id}/{job_id}/uploads/{filename}

This endpoint is called by the browser upload UI (upload_ui.py). Because it writes
to PostgreSQL, get_session_uploads works correctly even when called from a separate
docker-exec stdio subprocess.

Returns:
  {"blob_url": "http://localhost:10000/...", "asset_id": "<uuid>"}

The localhost:10000 URL is intentional — ingest_video._normalise_source_url
remaps it to azurite:10000 for container-to-container access.
"""
from __future__ import annotations

import asyncio
import io
import logging
import pathlib
import uuid

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse

from app.config import settings
from app import db

logger = logging.getLogger(__name__)

upload_router = APIRouter()

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".ts", ".mts", ".m2ts"}


def _is_video(filename: str, content_type: str | None) -> bool:
    if content_type and content_type.startswith("video/"):
        return True
    return pathlib.Path(filename).suffix.lower() in _VIDEO_EXTS


@upload_router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    filename: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
    job_id: str | None = Form(default=None),
) -> JSONResponse:
    effective_filename = filename or file.filename or "upload"
    sid = session_id or str(uuid.uuid4())
    jid = job_id or "unscoped"
    blob_name = f"videos/external/{sid}/{jid}/uploads/{effective_filename}"

    logger.info("Receiving upload: %s → %s/%s", effective_filename, settings.upload_container, blob_name)

    data = await file.read()

    try:
        await asyncio.to_thread(
            _upload_sync, blob_name, data,
            settings.azure_storage_connection_string,
            settings.upload_container,
        )
    except Exception as exc:
        logger.exception("Blob upload failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Host-facing URL — ingest_video._normalise_source_url remaps localhost:10000 → azurite:10000
    blob_url = (
        f"http://localhost:10000/devstoreaccount1/{settings.upload_container}/{blob_name}"
    )

    # Write DB records (same pattern as ingest_video + api-gateway)
    asset_id = str(uuid.uuid4())
    content_type = file.content_type or "application/octet-stream"
    asset_type = "uploaded_video" if _is_video(effective_filename, content_type) else "uploaded_file"

    try:
        await db.ensure_session(sid)
        await db.insert_asset(asset_id, sid, blob_url, effective_filename, content_type, jid if job_id else None)
        await db.insert_session_asset(sid, asset_id, blob_url, effective_filename, content_type, asset_type)
    except Exception as exc:
        # DB write failure is non-fatal for the upload itself, but log it prominently
        logger.error("DB record write failed (upload succeeded): %s", exc, exc_info=True)

    logger.info("Upload complete → %s (asset_id=%s, type=%s)", blob_url, asset_id, asset_type)
    return JSONResponse({"blob_url": blob_url, "asset_id": asset_id})


def _upload_sync(blob_name: str, data: bytes, connection_string: str, container_name: str) -> None:
    """Synchronous blob upload — runs in a thread pool to avoid blocking the event loop."""
    from azure.storage.blob import BlobServiceClient

    client = BlobServiceClient.from_connection_string(connection_string)
    container = client.get_container_client(container_name)
    try:
        container.create_container()
    except Exception:
        pass  # already exists
    blob_client = container.get_blob_client(blob_name)
    blob_client.upload_blob(io.BytesIO(data), overwrite=True, max_concurrency=4)
