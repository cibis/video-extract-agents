"""generated_asset_store — write/read intermediate job data assets in Blob Storage.

Blob path convention:
    generated/{session_id}/{job_id}/{data_type}/{filename}
"""
import json
import logging
from typing import Any
from urllib.parse import urlparse

from azure.storage.blob.aio import BlobServiceClient

from app.config import settings

logger = logging.getLogger(__name__)


def _blob_path(session_id: str | None, job_id: str, data_type: str, filename: str) -> str:
    scope = session_id or "unscoped"
    return f"generated/{scope}/{job_id}/{data_type}/{filename}"


def _parse_container_blob(url: str) -> tuple[str, str]:
    """Parse container name and blob path from a blob URL (Azurite and Azure)."""
    parsed = urlparse(url)
    parts = parsed.path.lstrip("/").split("/", 2)
    if len(parts) == 3:
        # Azurite: /<account>/<container>/<blob>
        return parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1]
    raise ValueError(f"Cannot parse container/blob from URL: {url}")


async def write_generated_asset(
    session_id: str | None,
    job_id: str,
    data_type: str,
    filename: str,
    data: dict | list,
) -> str:
    """Serialise data as JSON and upload to blob. Returns the blob URL."""
    blob_path = _blob_path(session_id, job_id, data_type, filename)
    content = json.dumps(data, ensure_ascii=False).encode("utf-8")
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        blob_client = client.get_blob_client(
            container=settings.azure_storage_container_name,
            blob=blob_path,
        )
        await blob_client.upload_blob(content, overwrite=True, content_type="application/json")
        logger.debug("write_generated_asset: wrote %d bytes to %s", len(content), blob_path)
        return blob_client.url


async def read_generated_asset(blob_url: str) -> Any:
    """Download and deserialise a generated asset blob."""
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        container, blob_name = _parse_container_blob(blob_url)
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        stream = await blob_client.download_blob()
        data = await stream.readall()
        return json.loads(data.decode("utf-8"))
