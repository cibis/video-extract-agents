#!/bin/bash
# Inspect e2e test results stored in the local PostgreSQL container.
# Run this after scripts/run-e2e-local.sh completes.
#
# Usage:
#   ./scripts/inspect-e2e-results.sh              # list recent test sessions + jobs
#   ./scripts/inspect-e2e-results.sh <session_id> # show job logs for one session
set -euo pipefail

CONTAINER="video-extract-postgresql"
DB="videoextract"
PSQL="docker exec -i $CONTAINER psql -U postgres -d $DB"

SESSION_ID="${1:-}"

if [ -z "$SESSION_ID" ]; then
  echo "==> Recent test sessions (last 20):"
  $PSQL -c "
    SELECT s.id AS session_id,
           s.created_at,
           COUNT(j.id) AS jobs,
           MAX(j.status) AS last_status
    FROM sessions s
    LEFT JOIN jobs j ON j.session_id = s.id
    WHERE s.is_test = TRUE
    GROUP BY s.id, s.created_at
    ORDER BY s.created_at DESC
    LIMIT 20;
  "

  echo ""
  echo "==> Recent test jobs:"
  $PSQL -c "
    SELECT j.id AS job_id,
           j.session_id,
           j.status,
           LEFT(j.prompt, 60) AS prompt,
           j.created_at
    FROM jobs j
    JOIN sessions s ON j.session_id = s.id
    WHERE s.is_test = TRUE
    ORDER BY j.created_at DESC
    LIMIT 20;
  "

  echo ""
  echo "Tip: pass a session_id to see full job logs:"
  echo "  $0 <session_id>"
else
  echo "==> Jobs for session $SESSION_ID:"
  $PSQL -c "
    SELECT id, status, LEFT(prompt, 80) AS prompt, output_url, created_at
    FROM jobs WHERE session_id = '$SESSION_ID' ORDER BY created_at;
  "

  echo ""
  echo "==> Job logs for session $SESSION_ID:"
  $PSQL -c "
    SELECT jl.created_at,
           jl.log_type,
           jl.tool_name,
           LEFT(jl.message, 120) AS message
    FROM job_logs jl
    JOIN jobs j ON jl.job_id = j.id
    WHERE j.session_id = '$SESSION_ID'
    ORDER BY jl.created_at;
  "

  echo ""
  echo "==> Session assets for session $SESSION_ID:"
  $PSQL -c "
    SELECT asset_type, filename, blob_url, created_at
    FROM session_assets WHERE session_id = '$SESSION_ID' ORDER BY created_at;
  "
fi
