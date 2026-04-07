"""asyncpg: fetch user and job data."""
import asyncpg
from app.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(url)
    return _pool


async def get_user_email(user_id: str) -> str | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT email FROM users WHERE id = $1", user_id)
    return row["email"] if row else None


async def get_job(job_id: str) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    return dict(row) if row else None
