"""Blob storage helpers for mcp-server-analysis."""
from __future__ import annotations

import logging

import httpx
from azure.storage.blob.aio import BlobServiceClient

from app.config import settings

logger = logging.getLogger(__name__)


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


def _is_blob_url(url: str) -> bool:
    return "blob.core.windows.net" in url or "127.0.0.1:10000" in url or "azurite" in url


async def _download_via_sdk(url: str, local_path: str) -> None:
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        # Parse container and blob name from URL path
        # URL format: http[s]://<host>/<container>/<blob>
        from urllib.parse import urlparse
        parsed = urlparse(url)
        # Path is /<account>/<container>/<blob...> for Azurite,
        # or /<container>/<blob...> for Azure
        parts = parsed.path.lstrip("/").split("/", 2)
        if len(parts) == 3:
            # Azurite: /<account>/<container>/<blob>
            container, blob_name = parts[1], parts[2]
        elif len(parts) == 2:
            container, blob_name = parts[0], parts[1]
        else:
            raise ValueError(f"Cannot parse container/blob from URL: {url}")

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
