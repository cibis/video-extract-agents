"""read_asset — read a non-video session asset from Blob Storage."""
from __future__ import annotations

import logging
import mimetypes

from app.blob import read_blob_bytes

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


async def read_asset(payload: dict) -> dict:
    """Download a blob asset and return its content as a string.

    Supports text formats (JSON, CSV, plain text) and returns raw bytes
    base64-encoded for binary formats.
    """
    blob_url: str = payload["blob_url"]
    max_bytes: int = payload.get("max_bytes") or _DEFAULT_MAX_BYTES

    raw_full = await read_blob_bytes(blob_url)
    raw = raw_full[:max_bytes]
    if len(raw_full) > max_bytes:
        logger.warning("Asset truncated at %d bytes: %s", max_bytes, blob_url)
    total = len(raw)
    # Derive content type from the URL extension; fall back to octet-stream.
    content_type, _ = mimetypes.guess_type(blob_url.split("?")[0])
    content_type = content_type or "application/octet-stream"

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
