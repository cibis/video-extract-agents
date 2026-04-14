"""Blob storage helpers for mcp-server-analysis."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from azure.storage.blob.aio import BlobServiceClient

from app.config import settings

logger = logging.getLogger(__name__)


def _is_blob_url(url: str) -> bool:
    return "blob.core.windows.net" in url or "127.0.0.1:10000" in url or "azurite" in url


def _parse_container_blob(url: str) -> tuple[str, str]:
    """Parse container and blob name from an Azure Blob Storage URL.

    Azure format:   https://<account>.blob.core.windows.net/<container>/<blob>
    Azurite format: http://azurite:10000/<account>/<container>/<blob>
                    http://127.0.0.1:10000/<account>/<container>/<blob>

    Azurite includes the account name as the first path segment; Azure does not.
    We detect Azurite by hostname so we can strip the account segment correctly.
    """
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    # Azurite exposes a virtual-path endpoint: first segment is the account name
    is_azurite = "127.0.0.1" in parsed.netloc or "azurite" in parsed.netloc
    if is_azurite:
        # Drop the account segment, remainder is <container>/<blob>
        _, path = path.split("/", 1)
    container, blob_name = path.split("/", 1)
    return container, blob_name


async def read_blob_bytes(url: str) -> bytes:
    """Return blob content as bytes.

    Uses the Azure Storage SDK (authenticated) for blob URLs so that frames
    stored in accounts with anonymous access disabled are still readable.
    Falls back to plain httpx for non-blob URLs (e.g. public CDN, Azurite with
    public access, external URLs).
    """
    if settings.azure_storage_connection_string and _is_blob_url(url):
        return await _read_via_sdk(url)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _read_via_sdk(url: str) -> bytes:
    container, blob_name = _parse_container_blob(url)
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        stream = await blob_client.download_blob()
        return await stream.readall()


async def download_blob(url: str, local_path: str) -> None:
    """Download a blob URL to a local file path.

    Supports:
    - Azure Blob Storage URLs (uses SDK when connection string is set)
    - Any HTTP/HTTPS URL (falls back to httpx streaming download)
    """
    if settings.azure_storage_connection_string and _is_blob_url(url):
        await _download_via_sdk(url, local_path)
    else:
        await _download_via_http(url, local_path)


async def _download_via_sdk(url: str, local_path: str) -> None:
    container, blob_name = _parse_container_blob(url)
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        blob_client = client.get_blob_client(container=container, blob=blob_name)
        with open(local_path, "wb") as f:
            stream = await blob_client.download_blob()
            async for chunk in stream.chunks():
                f.write(chunk)


async def _download_via_http(url: str, local_path: str) -> None:
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(local_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
