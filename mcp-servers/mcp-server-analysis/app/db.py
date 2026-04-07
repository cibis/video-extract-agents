"""Minimal PostgreSQL access for mcp-server-analysis (app_settings only)."""
import logging
from typing import Any

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(url)
    return _pool


async def get_app_setting(key: str) -> str | None:
    """Fetch a single app_settings value by key. Returns None if not found."""
    try:
        pool = await get_pool()
        row = await pool.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
        return row["value"] if row else None
    except Exception:
        logger.warning("get_app_setting(%s) failed", key, exc_info=True)
        return None


async def get_model_context_window(model_name: str) -> tuple[int, float]:
    """Read (context_window_tokens, safety_margin) from model_context_windows.

    Returns (128_000, 0.5) if the model is not in the table.
    Always queries DB — no caching — so runtime updates take effect immediately.
    """
    try:
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT context_window_tokens, safety_margin FROM model_context_windows WHERE model_name = $1",
            model_name,
        )
        if row:
            return int(row["context_window_tokens"]), float(row["safety_margin"])
        logger.warning(
            "get_model_context_window: model %r not found in model_context_windows; using default %d tokens",
            model_name, 128_000,
        )
    except Exception:
        logger.warning("get_model_context_window(%s) failed", model_name, exc_info=True)
    return 128_000, 0.5


async def get_keyframe_index(video_url: str) -> list[dict[str, Any]]:
    """Fetch keyframe index for a video by its original URL."""
    try:
        pool = await get_pool()
        rows = await pool.fetch(
            """SELECT ki.frame_index, ki.frame_url, ki.timestamp_seconds
               FROM video_keyframe_index ki
               JOIN videos v ON v.id = ki.video_id
               WHERE v.original_url = $1
               ORDER BY ki.frame_index""",
            video_url,
        )
        return [dict(r) for r in rows]
    except Exception:
        logger.warning("get_keyframe_index(%s) failed", video_url, exc_info=True)
        return []
