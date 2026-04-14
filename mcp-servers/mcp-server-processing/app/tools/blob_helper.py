"""Shared Blob Storage upload helper for processing tools."""
import mimetypes
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings, generate_blob_sas, BlobSasPermissions
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


def _parse_container_blob(url: str) -> tuple[str, str]:
    """Parse container name and blob path from a blob URL (Azurite and Azure)."""
    parsed = urlparse(url)
    parts = parsed.path.lstrip("/").split("/", 2)
    if len(parts) == 3 and parts[0] == "devstoreaccount1":
        return parts[1], parts[2]
    container = parts[0]
    blob = "/".join(parts[1:])
    if not container or not blob:
        raise ValueError(f"Cannot parse container/blob from URL: {url}")
    return container, blob


def _parse_connection_string(conn_str: str) -> dict[str, str]:
    """Parse key=value pairs from a storage connection string."""
    return dict(item.split("=", 1) for item in conn_str.split(";") if "=" in item)


def get_ffmpeg_accessible_url(blob_url: str, expiry_hours: int = 1) -> str:
    """Return a URL that FFmpeg can access.

    Azurite URLs (local dev) are returned unchanged — Azurite allows anonymous access
    inside Docker and this preserves existing local behaviour.
    Azure blob URLs get a short-lived SAS read token so FFmpeg can authenticate.
    """
    if "azurite" in blob_url:
        return blob_url
    container, blob_name = _parse_container_blob(blob_url)
    parts = _parse_connection_string(settings.azure_storage_connection_string)
    account_name = parts["AccountName"]
    account_key = parts["AccountKey"]
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
    )
    parsed = urlparse(blob_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"{base_url}?{sas_token}"


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
