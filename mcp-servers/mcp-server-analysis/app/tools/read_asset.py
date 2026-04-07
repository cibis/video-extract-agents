"""read_asset — read a non-video session asset from Blob Storage."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


async def read_asset(payload: dict) -> dict:
    """Download a blob asset and return its content as a string.

    Supports text formats (JSON, CSV, plain text) and returns raw bytes
    base64-encoded for binary formats.
    """
    blob_url: str = payload["blob_url"]
    max_bytes: int = payload.get("max_bytes") or _DEFAULT_MAX_BYTES

    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream("GET", blob_url) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "application/octet-stream")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(chunk_size=8192):
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    logger.warning("Asset truncated at %d bytes: %s", max_bytes, blob_url)
                    break
            raw = b"".join(chunks)

    is_text = any(
        t in content_type
        for t in ("text/", "application/json", "application/xml", "application/csv")
    )

    if is_text:
        content = raw.decode("utf-8", errors="replace")
    else:
        import base64
        content = base64.b64encode(raw).decode()

    return {
        "content": content,
        "content_type": content_type,
        "size_bytes": total,
    }
