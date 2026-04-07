"""asyncpg: store keyframe index in video_keyframe_index table."""
import asyncpg
from app.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(url)
    return _pool


async def store_keyframe_index(
    video_id: str,
    keyframes: list[dict],
) -> None:
    """Insert keyframe records for a video."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO video_keyframe_index
               (video_id, frame_index, frame_url, timestamp_seconds)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT DO NOTHING""",
            [
                (video_id, kf["frame_index"], kf["frame_url"], kf["timestamp_seconds"])
                for kf in keyframes
            ],
        )


async def get_app_setting(key: str, default: str) -> str:
    """Fetch a single value from app_settings, returning default if the key is absent."""
    pool = await get_pool()
    row = await pool.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
    return row["value"] if row else default


async def update_video_status(video_id: str, status: str) -> None:
    pool = await get_pool()
    await pool.execute(
        "UPDATE videos SET status = $1 WHERE id = $2",
        status,
        video_id,
    )


async def create_session_asset(
    session_id: str,
    asset_type: str,
    blob_url: str,
    source_id: str,
    filename: str | None = None,
    content_type: str | None = None,
    label: str | None = None,
) -> None:
    """Upsert a row into session_assets; on duplicate (session_id, source_id) update the label."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO session_assets
               (session_id, asset_type, blob_url, source_id, filename, content_type, label)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (session_id, source_id) DO UPDATE SET label = EXCLUDED.label""",
            session_id,
            asset_type,
            blob_url,
            source_id,
            filename,
            content_type,
            label,
        )
