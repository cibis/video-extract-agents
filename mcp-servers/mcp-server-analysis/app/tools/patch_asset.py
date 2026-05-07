"""patch_asset — apply RFC 6902 JSON Patch operations to a JSON blob in-place."""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

import jsonpatch
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

from app.config import settings

logger = logging.getLogger(__name__)


def _parse_blob_path(blob_url: str) -> str:
    container = settings.azure_storage_container_name
    path = urlparse(blob_url).path
    marker = f"/{container}/"
    idx = path.find(marker)
    if idx == -1:
        raise ValueError(f"Container '{container}' not found in blob URL: {blob_url}")
    return path[idx + len(marker):]


async def patch_asset(payload: dict[str, Any]) -> dict:
    """Read a JSON blob, apply RFC 6902 patch operations, write back in-place.

    Returns a brief summary only — never returns the modified content.
    """
    blob_url: str = payload["blob_url"]
    operations: list[dict] = payload["operations"]

    blob_path = _parse_blob_path(blob_url)

    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        stream = await client.get_blob_client(
            container=settings.azure_storage_container_name, blob=blob_path
        ).download_blob()
        raw = await stream.readall()

    doc = json.loads(raw)
    patch = jsonpatch.JsonPatch(operations)
    patched = patch.apply(doc)
    encoded = json.dumps(patched, ensure_ascii=False, indent=2).encode("utf-8")

    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        await client.get_blob_client(
            container=settings.azure_storage_container_name, blob=blob_path
        ).upload_blob(
            encoded,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )

    logger.info("patch_asset: %d op(s) applied to %s (%d bytes)", len(operations), blob_url, len(encoded))
    return {
        "blob_url": blob_url,
        "operations_applied": len(operations),
        "size_bytes": len(encoded),
    }
