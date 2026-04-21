"""write_asset — persist a generated non-video asset to Blob Storage."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

from app.config import settings

logger = logging.getLogger(__name__)


async def write_asset(payload: dict[str, Any]) -> dict:
    """Upload a text/JSON asset to Blob Storage and return its blob URL.

    Input:
      content: str
      filename: str
      content_type: str (optional, default application/json)
      job_id: str (optional)
      session_id: str (optional)
      description: str (optional) — human-readable description stored in return value
      summary: dict (optional) — structured summary stored in return value

    Blob path: assets/{session_id}/{job_id}/{filename}         when job_id is provided
              or assets/{session_id}/{uuid}/{filename}         when no job_id
    """
    content: str = payload["content"]
    filename: str = payload["filename"]
    content_type: str = payload.get("content_type", "application/json")
    job_id: str | None = payload.get("job_id") or None
    session_id: str | None = payload.get("session_id") or None
    description: str | None = payload.get("description") or None
    summary: dict | None = payload.get("summary") or None
    scope = session_id or "unscoped"
    if job_id:
        blob_path = f"assets/{scope}/{job_id}/{filename}"
    else:
        asset_id = str(uuid.uuid4())
        blob_path = f"assets/{scope}/{asset_id}/{filename}"

    encoded = content.encode("utf-8")

    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        blob_client = client.get_blob_client(
            container=settings.azure_storage_container_name,
            blob=blob_path,
        )
        await blob_client.upload_blob(
            encoded,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        blob_url = blob_client.url

    logger.info("Wrote asset %s (%d bytes) to %s", filename, len(encoded), blob_url)
    result: dict[str, Any] = {
        "blob_url": blob_url,
        "filename": filename,
        "size_bytes": len(encoded),
    }
    if description is not None:
        result["description"] = description
    if summary is not None:
        result["summary"] = summary
    return result
