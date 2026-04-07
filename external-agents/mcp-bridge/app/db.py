"""PostgreSQL access for mcp-bridge.

Reuses the same asyncpg pool pattern as mcp-server-analysis/app/db.py.
Provides write helpers for the upload endpoint and a read helper for
get_session_uploads — used by both the uvicorn HTTP process and any stdio
subprocess (docker exec) so uploads are always visible across processes.
"""
from __future__ import annotations

import logging
import uuid

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

_LOCAL_DEV_USER_ID = "00000000-0000-0000-0000-000000000001"

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(url)
    return _pool


async def ensure_session(session_id: str) -> None:
    """Create the session row if it does not yet exist."""
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO sessions (id, user_id)
           VALUES ($1::uuid, $2::uuid)
           ON CONFLICT (id) DO NOTHING""",
        session_id, _LOCAL_DEV_USER_ID,
    )


async def insert_asset(
    asset_id: str,
    session_id: str,
    blob_url: str,
    filename: str,
    content_type: str,
    source_job_id: str | None,
) -> None:
    """Insert a row into the assets table."""
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO assets
               (id, user_id, session_id, blob_url, filename, content_type, source, source_job_id)
           VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'upload', $7::uuid)
           ON CONFLICT (id) DO NOTHING""",
        asset_id, _LOCAL_DEV_USER_ID, session_id,
        blob_url, filename, content_type,
        source_job_id if source_job_id else None,
    )


async def insert_session_asset(
    session_id: str,
    asset_id: str,
    blob_url: str,
    filename: str,
    content_type: str,
    asset_type: str,
) -> None:
    """Insert a row into the session_assets table."""
    pool = await get_pool()
    row_id = str(uuid.uuid4())
    await pool.execute(
        """INSERT INTO session_assets
               (id, session_id, asset_type, blob_url, filename, content_type, source_id)
           VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::uuid)
           ON CONFLICT (session_id, source_id) DO NOTHING""",
        row_id, session_id, asset_type,
        blob_url, filename, content_type, asset_id,
    )


async def get_session_uploads(session_id: str) -> list[dict]:
    """Return all uploaded assets for a session (uploaded_video + uploaded_file)."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT source_id AS asset_id, blob_url, filename, content_type, created_at
           FROM session_assets
           WHERE session_id = $1::uuid
             AND asset_type IN ('uploaded_video', 'uploaded_file')
           ORDER BY created_at""",
        session_id,
    )
    return [
        {
            "asset_id": str(r["asset_id"]),
            "blob_url": r["blob_url"],
            "filename": r["filename"],
            "content_type": r["content_type"],
            "uploaded_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
