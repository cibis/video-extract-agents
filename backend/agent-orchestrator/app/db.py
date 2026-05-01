"""PostgreSQL access layer using asyncpg."""
import asyncio
import asyncpg
import json
from datetime import datetime, timezone
from typing import Any
from app.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # Convert SQLAlchemy-style URL to asyncpg URL
        url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(url)
    return _pool


async def get_job(job_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
    return dict(row) if row else None


async def update_job_status(
    job_id: str,
    status: str,
    output_url: str | None = None,
    error: str | None = None,
) -> None:
    pool = await get_pool()
    await pool.execute(
        """UPDATE jobs
           SET status = $1, output_url = $2, error = $3, updated_at = NOW()
           WHERE id = $4""",
        status,
        output_url,
        error,
        job_id,
    )


async def get_keyframe_index(video_url: str) -> list[dict[str, Any]]:
    """Fetch keyframe index for a video identified by its original URL."""
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


async def get_keyframe_indices_for_videos(video_urls: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Fetch keyframe indices for multiple videos, keyed by original URL."""
    if not video_urls:
        return {}
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT v.original_url, ki.frame_index, ki.frame_url, ki.timestamp_seconds
           FROM video_keyframe_index ki
           JOIN videos v ON v.id = ki.video_id
           WHERE v.original_url = ANY($1::text[])
           ORDER BY v.original_url, ki.frame_index""",
        video_urls,
    )
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        url = row["original_url"]
        if url not in result:
            result[url] = []
        result[url].append({
            "frame_index": row["frame_index"],
            "frame_url": row["frame_url"],
            "timestamp_seconds": row["timestamp_seconds"],
        })
    return result


async def get_unindexed_video_urls(video_urls: list[str]) -> list[str]:
    """Return the subset of video_urls whose videos.status is not yet 'indexed'.

    The preprocessing worker sets status='indexed' only after it has committed
    all keyframe rows, so an empty return value guarantees the keyframe index
    is complete for every URL in the list.
    """
    if not video_urls:
        return []
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT original_url FROM videos
           WHERE original_url = ANY($1::text[]) AND status != 'indexed'""",
        video_urls,
    )
    return [row["original_url"] for row in rows]


async def get_session_assets(session_id: str) -> list[dict[str, Any]]:
    """Fetch visible (non-hidden) assets registered under a session."""
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, asset_type, blob_url, filename, content_type, source_id, label,
                  metadata_json, description, summary_json, source_job_id, created_at
           FROM session_assets
           WHERE session_id = $1 AND session_hidden = false
           ORDER BY created_at""",
        session_id,
    )
    return [dict(r) for r in rows]


async def unhide_session_asset_by_url(session_id: str, blob_url: str) -> None:
    """Reveal a previously-hidden analysis asset when a cache hit confirms it was re-run."""
    if not session_id or not blob_url:
        return
    try:
        pool = await get_pool()
        await pool.execute(
            """UPDATE session_assets
                  SET session_hidden = false
                WHERE session_id = $1 AND blob_url = $2 AND session_hidden = true""",
            session_id,
            blob_url,
        )
    except Exception:
        pass


async def get_job_summary(job_id: str) -> dict[str, Any] | None:
    """Fetch a lean summary of a job and its outputs for use as follow-up context."""
    pool = await get_pool()
    job_row, output_rows, file_rows = await asyncio.gather(
        pool.fetchrow(
            "SELECT id, prompt, status, error, parent_job_id FROM jobs WHERE id = $1",
            job_id,
        ),
        pool.fetch(
            "SELECT blob_url, filename, content_type FROM outputs"
            " WHERE job_id = $1 ORDER BY created_at",
            job_id,
        ),
        pool.fetch(
            "SELECT blob_url, filename, content_type FROM session_assets"
            " WHERE label = $1 AND asset_type = 'job_output_file' ORDER BY created_at",
            f"job:{job_id}",
        ),
    )
    if not job_row:
        return None
    return {
        "job_id": str(job_row["id"]),
        "prompt": job_row["prompt"],
        "status": job_row["status"],
        "error": job_row["error"],
        "parent_job_id": str(job_row["parent_job_id"]) if job_row["parent_job_id"] else None,
        "outputs": [
            {"blob_url": r["blob_url"], "filename": r["filename"], "content_type": r["content_type"]}
            for r in output_rows
        ],
        "generated_files": [
            {"blob_url": r["blob_url"], "filename": r["filename"], "content_type": r["content_type"]}
            for r in file_rows
        ],
    }


async def get_job_asset_manifest(job_id: str) -> dict[str, Any] | None:
    """Return a job's prompt and all associated blob assets (no content).

    Includes video outputs, generated files, and any session_assets linked to
    this job via source_job_id.  Used by _load_job_history() in crew.py to
    build multi-turn context for follow-up jobs.
    """
    pool = await get_pool()
    job_row, output_rows, asset_rows = await asyncio.gather(
        pool.fetchrow(
            "SELECT id, prompt, status, error, parent_job_id FROM jobs WHERE id = $1",
            job_id,
        ),
        pool.fetch(
            "SELECT blob_url, filename, content_type FROM outputs"
            " WHERE job_id = $1 ORDER BY created_at",
            job_id,
        ),
        pool.fetch(
            """SELECT blob_url, filename, content_type, asset_type, description, summary_json
               FROM session_assets
               WHERE source_job_id = $1
               ORDER BY created_at""",
            job_id,
        ),
    )
    if not job_row:
        return None
    return {
        "job_id": str(job_row["id"]),
        "prompt": job_row["prompt"],
        "status": job_row["status"],
        "error": job_row["error"],
        "parent_job_id": str(job_row["parent_job_id"]) if job_row["parent_job_id"] else None,
        "outputs": [
            {"blob_url": r["blob_url"], "filename": r["filename"], "content_type": r["content_type"]}
            for r in output_rows
        ],
        "assets": [
            {
                "blob_url": r["blob_url"],
                "filename": r["filename"],
                "content_type": r["content_type"],
                "asset_type": r["asset_type"],
                "description": r["description"],
                "summary": json.loads(r["summary_json"]) if r["summary_json"] else None,
            }
            for r in asset_rows
        ],
    }


async def create_output(
    job_id: str,
    session_id: str | None,
    blob_url: str,
    filename: str | None = None,
    content_type: str = "video/mp4",
) -> str:
    """Insert an output record and return its id."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """INSERT INTO outputs (job_id, session_id, blob_url, filename, content_type)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING id""",
        job_id,
        session_id,
        blob_url,
        filename,
        content_type,
    )
    return str(row["id"])


async def create_session_asset(
    session_id: str,
    asset_type: str,
    blob_url: str,
    source_id: str,
    filename: str | None = None,
    content_type: str | None = None,
    label: str | None = None,
    description: str | None = None,
    summary_json: dict | None = None,
    source_job_id: str | None = None,
) -> None:
    """Insert a row into session_assets, ignoring duplicates (same session_id + source_id)."""
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO session_assets
           (session_id, asset_type, blob_url, source_id, filename, content_type, label,
            description, summary_json, source_job_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
           ON CONFLICT (session_id, source_id) DO NOTHING""",
        session_id,
        asset_type,
        blob_url,
        source_id,
        filename,
        content_type,
        label,
        description,
        json.dumps(summary_json) if summary_json else None,
        source_job_id,
    )


async def record_job_log(
    job_id: str | None,
    session_id: str | None,
    service_name: str,
    log_type: str,
    model_id: str | None = None,
    tool_name: str | None = None,
    agent_role: str | None = None,
    task_name: str | None = None,
    message: str | None = None,
    message_type: str = "Output",
    call_group_id: str | None = None,
    sequence_num: int = 0,
    error_text: str | None = None,
    cached: bool = False,
) -> None:
    """Insert one row into job_logs. Silently ignores missing job_id or errors."""
    if not job_id:
        return
    try:
        pool = await get_pool()
        now = datetime.now(timezone.utc)
        await pool.execute(
            """INSERT INTO job_logs
               (job_id, session_id, service_name, log_type, model_id, tool_name,
                agent_role, task_name, message, message_type, call_group_id,
                sequence_num, error_text, created_at, cached)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)""",
            job_id,
            session_id,
            service_name,
            log_type,
            model_id,
            tool_name,
            agent_role,
            task_name,
            message,
            message_type,
            call_group_id,
            sequence_num,
            error_text,
            now,
            cached,
        )
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("record_job_log failed", exc_info=True)


async def get_app_setting(key: str) -> str | None:
    """Fetch a single app_settings value by key. Returns None if not found."""
    try:
        pool = await get_pool()
        row = await pool.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
        return row["value"] if row else None
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("get_app_setting(%s) failed", key, exc_info=True)
        return None


async def record_job_step(
    job_id: str,
    step_name: str,
    status: str = "completed",
    result: dict | None = None,
) -> None:
    """Insert a progress step row into job_steps. Silently ignores errors."""
    try:
        pool = await get_pool()
        await pool.execute(
            """INSERT INTO job_steps (job_id, step_name, status, result)
               VALUES ($1, $2, $3, $4)""",
            job_id,
            step_name,
            status,
            json.dumps(result) if result else None,
        )
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).debug("record_job_step failed", exc_info=True)


async def insert_tool_progress(
    call_group_id: str,
    job_id: str,
    tool_name: str,
) -> None:
    """Insert the initial tool_progress row when a tool call starts."""
    if not job_id:
        return
    try:
        pool = await get_pool()
        await pool.execute(
            """INSERT INTO tool_progress
               (call_group_id, job_id, tool_name, processed_units, status, started_at, updated_at)
               VALUES ($1, $2, $3, 0, 'running', NOW(), NOW())
               ON CONFLICT (call_group_id) DO NOTHING""",
            call_group_id, job_id, tool_name,
        )
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("insert_tool_progress failed", exc_info=True)


async def upsert_tool_progress(
    call_group_id: str,
    processed_units: int,
    total_units: int | None = None,
    unit_label: str = "items",
) -> None:
    """Update processed_units for a running tool call. Called on each progress event."""
    try:
        pool = await get_pool()
        await pool.execute(
            """UPDATE tool_progress
               SET processed_units = $2,
                   total_units     = COALESCE($3, total_units),
                   unit_label      = $4,
                   updated_at      = NOW()
               WHERE call_group_id = $1""",
            call_group_id, processed_units, total_units, unit_label,
        )
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("upsert_tool_progress failed", exc_info=True)


async def complete_tool_progress(
    call_group_id: str,
    success: bool = True,
) -> None:
    """Mark a tool_progress row as completed or failed."""
    try:
        pool = await get_pool()
        await pool.execute(
            "UPDATE tool_progress SET status=$2, updated_at=NOW() WHERE call_group_id=$1",
            call_group_id, "completed" if success else "failed",
        )
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("complete_tool_progress failed", exc_info=True)


async def create_generated_asset(
    user_id: str,
    session_id: str | None,
    blob_url: str,
    filename: str,
    content_type: str,
    source_job_id: str | None = None,
    description: str | None = None,
) -> str:
    """Insert into assets table for generated non-video files. Returns asset id."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """INSERT INTO assets
           (user_id, session_id, blob_url, filename, content_type, source, source_job_id, description)
           VALUES ($1, $2, $3, $4, $5, 'generated', $6, $7)
           RETURNING id""",
        user_id,
        session_id,
        blob_url,
        filename,
        content_type,
        source_job_id,
        description,
    )
    return str(row["id"])


# ─── Tool call cache ──────────────────────────────────────────────────────────

async def get_tool_cache(user_id: str, tool_name: str, input_hash: str) -> dict[str, Any] | None:
    """Return cached tool output for (user_id, tool_name, input_hash), or None on miss."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT output_json FROM tool_call_cache WHERE user_id=$1 AND tool_name=$2 AND input_hash=$3",
        user_id,
        tool_name,
        input_hash,
    )
    if row is None:
        return None
    val = row["output_json"]
    if isinstance(val, str):
        return json.loads(val)
    return dict(val)


async def set_tool_cache(
    user_id: str,
    tool_name: str,
    input_hash: str,
    input_json: dict[str, Any],
    output_json: dict[str, Any],
) -> None:
    """Persist a tool call result. Silently ignores conflicts (same result already cached)."""
    pool = await get_pool()
    await pool.execute(
        """INSERT INTO tool_call_cache (user_id, tool_name, input_hash, input_json, output_json)
           VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
           ON CONFLICT (user_id, tool_name, input_hash) DO NOTHING""",
        user_id,
        tool_name,
        input_hash,
        json.dumps(input_json),
        json.dumps(output_json),
    )
