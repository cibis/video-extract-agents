#!/usr/bin/env python3
"""
Repair an orphaned job whose output video was successfully written to blob storage
but whose job record was never updated (status=failed, output_url=NULL).

Usage:
  python scripts/repair_job_output.py <job_id> <output_url>

Example:
  python scripts/repair_job_output.py \
    22bbc9d2-2a63-4111-814f-131f7ce30cbe \
    "http://azurite:10000/devstoreaccount1/videos/processed/outputs/white_dogs_and_cats.mp4"
"""
import asyncio
import os
import sys
import uuid

import asyncpg

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/videoextract",
)


async def repair(job_id: str, output_url: str) -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Fetch the job to confirm it exists and get session_id
        row = await conn.fetchrow(
            "SELECT id, session_id, status, output_url FROM jobs WHERE id = $1",
            job_id,
        )
        if not row:
            print(f"ERROR: job {job_id} not found.")
            sys.exit(1)

        print(f"Job found — status={row['status']}, output_url={row['output_url']}")

        if row["output_url"]:
            print("Job already has output_url set. Nothing to repair.")
            return

        session_id: str | None = str(row["session_id"]) if row["session_id"] else None
        filename = f"job_{job_id}_output.mp4"

        # Update job to completed
        await conn.execute(
            """UPDATE jobs
               SET status = 'completed', output_url = $1, completed_at = now(), error = NULL
               WHERE id = $2""",
            output_url,
            job_id,
        )
        print(f"Updated jobs row: status=completed, output_url={output_url}")

        # Insert outputs row
        output_id = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO outputs (id, job_id, session_id, blob_url, filename, content_type)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT DO NOTHING""",
            output_id,
            job_id,
            session_id,
            output_url,
            filename,
            "video/mp4",
        )
        print(f"Inserted outputs row: id={output_id}")

        # Insert session_assets row (only if session_id exists)
        if session_id:
            await conn.execute(
                """INSERT INTO session_assets
                   (session_id, asset_type, blob_url, source_id, filename, content_type, label)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (session_id, source_id) DO NOTHING""",
                session_id,
                "job_output_video",
                output_url,
                output_id,
                filename,
                "video/mp4",
                f"job:{job_id}",
            )
            print(f"Inserted session_assets row for session {session_id}")
        else:
            print("No session_id on job — skipping session_assets insert.")

        print("\nRepair complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    asyncio.run(repair(sys.argv[1], sys.argv[2]))
