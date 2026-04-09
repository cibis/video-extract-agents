"""Shared Blob Storage upload helper for processing tools."""
import mimetypes
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings
from app.config import settings


async def upload_to_blob(local_path: str, blob_path: str) -> str:
    """Upload a local file to Blob Storage and return its URL."""
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        blob_client = client.get_blob_client(
            container=settings.azure_storage_container_name,
            blob=blob_path,
        )
        with open(local_path, "rb") as f:
            await blob_client.upload_blob(
                f,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
        return blob_client.url


def make_blob_path(
    prefix: str,
    name: str,
    ext: str = "mp4",
    job_id: str | None = None,
    session_id: str | None = None,
) -> str:
    if job_id:
        scope = session_id or "unscoped"
        return f"processed/{scope}/{job_id}/{prefix}/{name}.{ext}"
    return f"processed/{prefix}/{name}.{ext}"
