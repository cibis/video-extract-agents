import { Pool } from 'pg';
import { config } from '../config';

let _pool: Pool | null = null;

export function getPool(): Pool {
  if (!_pool) {
    _pool = new Pool({ connectionString: config.DATABASE_URL });
  }
  return _pool;
}

// ─── User provisioning ────────────────────────────────────────────────────────

const _knownUsers = new Set<string>();

export async function upsertUser(id: string, email: string): Promise<void> {
  if (_knownUsers.has(id)) return;
  const pool = getPool();
  await pool.query(
    `INSERT INTO users (id, email) VALUES ($1, $2)
     ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email`,
    [id, email],
  );
  _knownUsers.add(id);
}

export async function findUserByEmail(email: string): Promise<{ id: string; email: string } | null> {
  const pool = getPool();
  const result = await pool.query<{ id: string; email: string }>(
    'SELECT id, email FROM users WHERE LOWER(email) = LOWER($1) LIMIT 1',
    [email],
  );
  return result.rows[0] ?? null;
}

// ─── Schema initialisation ────────────────────────────────────────────────────

const SCHEMA_SQL = `
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    is_test BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions(user_id);

CREATE TABLE IF NOT EXISTS videos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    original_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS videos_user_id_idx ON videos(user_id);
CREATE INDEX IF NOT EXISTS videos_session_id_idx ON videos(session_id);
CREATE INDEX IF NOT EXISTS videos_status_idx ON videos(status);

CREATE TABLE IF NOT EXISTS video_keyframe_index (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID REFERENCES videos(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    frame_url TEXT NOT NULL,
    timestamp_seconds FLOAT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(video_id, frame_index)
);
CREATE INDEX IF NOT EXISTS keyframe_video_id_idx ON video_keyframe_index(video_id);

CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    blob_url TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_size_bytes BIGINT,
    source TEXT NOT NULL DEFAULT 'upload',
    source_job_id UUID,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS assets_user_id_idx ON assets(user_id);
CREATE INDEX IF NOT EXISTS assets_session_id_idx ON assets(session_id);

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    parent_job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    video_id UUID REFERENCES videos(id) ON DELETE SET NULL,
    video_ids UUID[],
    prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    output_url TEXT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS jobs_user_id_idx ON jobs(user_id);
CREATE INDEX IF NOT EXISTS jobs_session_id_idx ON jobs(session_id);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);

CREATE TABLE IF NOT EXISTS job_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS job_steps_job_id_idx ON job_steps(job_id);

CREATE TABLE IF NOT EXISTS outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    blob_url TEXT NOT NULL,
    filename TEXT,
    content_type TEXT DEFAULT 'video/mp4',
    signed_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS outputs_job_id_idx ON outputs(job_id);
CREATE INDEX IF NOT EXISTS outputs_session_id_idx ON outputs(session_id);

CREATE TABLE IF NOT EXISTS session_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL,
    blob_url TEXT NOT NULL,
    filename TEXT,
    content_type TEXT,
    source_id UUID,
    label TEXT,
    metadata_json JSONB,
    description TEXT,
    summary_json JSONB,
    source_job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS session_assets_session_id_idx ON session_assets(session_id);
CREATE INDEX IF NOT EXISTS session_assets_asset_type_idx ON session_assets(asset_type);
CREATE INDEX IF NOT EXISTS session_assets_source_job_id_idx ON session_assets(source_job_id);
CREATE UNIQUE INDEX IF NOT EXISTS session_assets_session_source_idx ON session_assets(session_id, source_id);

CREATE TABLE IF NOT EXISTS job_logs (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        UUID        REFERENCES jobs(id) ON DELETE CASCADE,
    session_id    UUID        REFERENCES sessions(id) ON DELETE SET NULL,
    service_name  TEXT        NOT NULL DEFAULT '',
    log_type      TEXT        NOT NULL,
    model_id      TEXT,
    tool_name     TEXT,
    agent_role    TEXT,
    task_name     TEXT,
    message       TEXT,
    message_type  TEXT        NOT NULL DEFAULT 'Output',
    call_group_id UUID        NOT NULL DEFAULT gen_random_uuid(),
    sequence_num  INTEGER     NOT NULL DEFAULT 0,
    error_text    TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS job_logs_job_id_idx        ON job_logs(job_id);
CREATE INDEX IF NOT EXISTS job_logs_session_id_idx    ON job_logs(session_id);
CREATE INDEX IF NOT EXISTS job_logs_call_group_id_idx ON job_logs(call_group_id);
CREATE INDEX IF NOT EXISTS job_logs_sequence_num_idx  ON job_logs(job_id, sequence_num);

CREATE TABLE IF NOT EXISTS tool_progress (
    call_group_id   UUID         PRIMARY KEY,
    job_id          UUID         REFERENCES jobs(id) ON DELETE CASCADE,
    tool_name       TEXT         NOT NULL,
    total_units     INTEGER,
    processed_units INTEGER      NOT NULL DEFAULT 0,
    unit_label      TEXT         NOT NULL DEFAULT 'items',
    status          TEXT         NOT NULL DEFAULT 'running',
    started_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS tool_progress_job_id_idx     ON tool_progress(job_id);
CREATE INDEX IF NOT EXISTS tool_progress_updated_at_idx ON tool_progress(job_id, updated_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS model_context_windows (
    model_name            TEXT PRIMARY KEY,
    context_window_tokens INTEGER NOT NULL,
    safety_margin         FLOAT NOT NULL DEFAULT 0.5,
    description           TEXT,
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_call_cache (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool_name   TEXT        NOT NULL,
    input_hash  TEXT        NOT NULL,
    input_json  JSONB       NOT NULL,
    output_json JSONB       NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS tool_call_cache_lookup_idx ON tool_call_cache (user_id, tool_name, input_hash);
CREATE INDEX IF NOT EXISTS tool_call_cache_user_id_idx ON tool_call_cache (user_id);

-- Seed dev user for LOCAL_DEV_SKIP_AUTH mode
INSERT INTO users (id, email) VALUES
    ('00000000-0000-0000-0000-000000000001', 'dev@local')
ON CONFLICT (id) DO NOTHING;
`;

/**
 * Ensure all tables exist. Safe to call on every startup — all statements are
 * CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / INSERT ON CONFLICT DO NOTHING.
 * Runs on every environment (local, CI test ACA, dev, prod).
 * Retries for up to 60 s to handle PostgreSQL not being ready yet (ACA cold start).
 */
export async function initializeSchema(): Promise<void> {
  const pool = getPool();
  const MAX_ATTEMPTS = 20;
  const DELAY_MS = 3_000;

  for (let i = 1; i <= MAX_ATTEMPTS; i++) {
    try {
      const client = await pool.connect();
      try {
        await client.query(SCHEMA_SQL);
        console.log('Database schema initialised.');
        return;
      } finally {
        client.release();
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (i < MAX_ATTEMPTS) {
        console.warn(`Schema init attempt ${i}/${MAX_ATTEMPTS} failed (${msg}) — retrying in ${DELAY_MS / 1000}s…`);
        await new Promise(r => setTimeout(r, DELAY_MS));
      } else {
        console.error('Schema initialisation failed after all attempts:', err);
        throw err;
      }
    }
  }
}

// ─── Interfaces ───────────────────────────────────────────────────────────────

export interface Session {
  id: string;
  user_id: string;
  is_test: boolean;
  created_at: Date;
  updated_at: Date;
}

export interface Job {
  id: string;
  user_id: string;
  session_id: string | null;
  parent_job_id: string | null;
  video_id: string | null;
  video_ids: string[] | null;
  prompt: string;
  status: 'draft' | 'queued' | 'processing' | 'completed' | 'failed';
  output_url: string | null;
  error: string | null;
  is_test: boolean;
  created_at: Date;
  updated_at: Date;
}

export interface Asset {
  id: string;
  user_id: string;
  session_id: string | null;
  blob_url: string;
  filename: string;
  content_type: string;
  file_size_bytes: number | null;
  source: 'upload' | 'generated';
  source_job_id: string | null;
  created_at: Date;
}

export interface Output {
  id: string;
  job_id: string;
  session_id: string | null;
  blob_url: string;
  filename: string | null;
  content_type: string;
  signed_url: string | null;
  created_at: Date;
}

export interface SessionAsset {
  id: string;
  session_id: string;
  asset_type: string;
  blob_url: string;
  filename: string | null;
  content_type: string | null;
  source_id: string | null;
  label: string | null;
  metadata_json: object | null;
  created_at: Date;
}

export interface JobLog {
  id: string;
  job_id: string;
  session_id: string | null;
  service_name: string;
  log_type: string;
  model_id: string | null;
  tool_name: string | null;
  agent_role: string | null;
  task_name: string | null;
  message: string | null;
  message_type: string;
  call_group_id: string;
  sequence_num: number;
  error_text: string | null;
  cached: boolean;
  created_at: Date;
}

export interface ToolProgress {
  call_group_id: string;
  job_id: string;
  tool_name: string;
  total_units: number | null;
  processed_units: number;
  unit_label: string;
  status: 'running' | 'completed' | 'failed';
  started_at: Date;
  updated_at: Date;
}

// ─── Sessions ─────────────────────────────────────────────────────────────────

export async function createSession(userId: string, isTest = false): Promise<Session> {
  const pool = getPool();
  const result = await pool.query<Session>(
    `INSERT INTO sessions (user_id, is_test) VALUES ($1, $2) RETURNING *`,
    [userId, isTest]
  );
  return result.rows[0];
}

/**
 * Creates the session row if it doesn't already exist.
 * Called before inserting child rows (videos, assets) when the client supplies
 * a session ID from localStorage that may have been deleted server-side.
 */
export async function ensureSessionExists(userId: string, sessionId: string): Promise<void> {
  const pool = getPool();
  await pool.query(
    `INSERT INTO sessions (id, user_id) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING`,
    [sessionId, userId],
  );
}

export async function getLatestSessionForUser(userId: string): Promise<Session | null> {
  const pool = getPool();
  const result = await pool.query<Session>(
    'SELECT * FROM sessions WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1',
    [userId]
  );
  return result.rows[0] ?? null;
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

export async function createJob(params: {
  id: string;
  userId: string;
  prompt?: string;
  status?: string;
  videoId?: string | null;
  sessionId?: string;
  parentJobId?: string;
  videoIds?: string[];
}): Promise<Job> {
  const pool = getPool();
  const result = await pool.query<Job>(
    `INSERT INTO jobs (id, user_id, video_id, video_ids, session_id, parent_job_id, prompt, status)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
     RETURNING *`,
    [
      params.id,
      params.userId,
      params.videoId,
      params.videoIds ?? [params.videoId],
      params.sessionId ?? null,
      params.parentJobId ?? null,
      params.prompt ?? '',
      params.status ?? 'queued',
    ]
  );
  return result.rows[0];
}

export async function updateJobPrompt(
  jobId: string,
  userId: string,
  prompt: string,
  sessionId?: string | null,
): Promise<void> {
  const pool = getPool();
  await pool.query(
    `UPDATE jobs
        SET prompt = $1, status = 'queued', updated_at = NOW(),
            session_id = COALESCE($4, session_id)
      WHERE id = $2 AND user_id = $3 AND status = 'draft'`,
    [prompt, jobId, userId, sessionId ?? null],
  );
}

export async function getJobById(jobId: string): Promise<Job | null> {
  const pool = getPool();
  const result = await pool.query<Job>(
    'SELECT * FROM jobs WHERE id = $1',
    [jobId]
  );
  return result.rows[0] ?? null;
}

/**
 * Returns the most recent draft or active (queued/processing) job for a session.
 * Used by the chat route to reuse the pre-created draft job when LibreChat calls
 * the API server-side (without a browser-injected jobId in the request body).
 */
export async function findActiveJobForSession(userId: string, sessionId: string): Promise<Job | null> {
  const pool = getPool();
  const result = await pool.query<Job>(
    `SELECT * FROM jobs
      WHERE user_id = $1 AND session_id = $2 AND status IN ('draft', 'queued', 'processing')
      ORDER BY created_at DESC LIMIT 1`,
    [userId, sessionId],
  );
  return result.rows[0] ?? null;
}

/**
 * Returns the most recent draft job with no session attached.
 * Used as a fallback in the chat route when the draft was created before the
 * user's first upload (so session_id is null on the draft but the chat request
 * carries a real session ID).
 */
export async function findDraftJobWithoutSession(userId: string): Promise<Job | null> {
  const pool = getPool();
  const result = await pool.query<Job>(
    `SELECT * FROM jobs
      WHERE user_id = $1 AND session_id IS NULL AND status = 'draft'
      ORDER BY created_at DESC LIMIT 1`,
    [userId],
  );
  return result.rows[0] ?? null;
}

/**
 * Returns the most recent draft job that references any session (not null).
 * Used in the chat route as a fallback when getLatestSessionForUser returns
 * null — this happens after switching from LOCAL_DEV_SKIP_AUTH=true to real
 * Entra auth, where the user's draft was linked to a session created under the
 * dev identity. The session_id from the draft is still valid for fetching
 * session assets and finding the right video context.
 */
export async function findLatestDraftJobWithSession(userId: string): Promise<Job | null> {
  const pool = getPool();
  const result = await pool.query<Job>(
    `SELECT * FROM jobs
      WHERE user_id = $1 AND session_id IS NOT NULL AND status = 'draft'
      ORDER BY created_at DESC LIMIT 1`,
    [userId],
  );
  return result.rows[0] ?? null;
}

export async function listJobsForUser(
  userId: string,
  filter: 'real' | 'test' | 'all' = 'all',
  limit = 50,
): Promise<Job[]> {
  const pool = getPool();
  const filterClause =
    filter === 'real' ? `AND COALESCE(s.is_test, FALSE) = FALSE` :
    filter === 'test' ? `AND COALESCE(s.is_test, FALSE) = TRUE`  : '';
  const result = await pool.query<Job>(
    `SELECT j.*, COALESCE(s.is_test, FALSE) AS is_test
     FROM jobs j
     LEFT JOIN sessions s ON j.session_id = s.id
     WHERE j.user_id = $1 AND j.status != 'draft'
     ${filterClause}
     ORDER BY j.created_at DESC LIMIT $2`,
    [userId, limit],
  );
  return result.rows;
}

export async function listTestSessionBlobs(userId: string): Promise<{
  sessionIds: string[];
  blobUrls: string[];
  blobPrefixes: string[];
}> {
  const pool = getPool();

  const sessionsRes = await pool.query<{ id: string }>(
    `SELECT id FROM sessions WHERE user_id = $1 AND is_test = TRUE`,
    [userId],
  );
  const sessionIds = sessionsRes.rows.map(r => r.id);
  if (sessionIds.length === 0) {
    return { sessionIds: [], blobUrls: [], blobPrefixes: [] };
  }

  const placeholders = sessionIds.map((_, i) => `$${i + 1}`).join(', ');

  const saRes = await pool.query<{ blob_url: string }>(
    `SELECT blob_url FROM session_assets WHERE session_id IN (${placeholders})`,
    sessionIds,
  );
  const outRes = await pool.query<{ blob_url: string }>(
    `SELECT blob_url FROM outputs
     WHERE job_id IN (SELECT id FROM jobs WHERE session_id IN (${placeholders}))`,
    sessionIds,
  );
  const kfRes = await pool.query<{ frame_url: string }>(
    `SELECT frame_url FROM video_keyframe_index
     WHERE video_id IN (SELECT id FROM videos WHERE session_id IN (${placeholders}))`,
    sessionIds,
  );
  const videoRes = await pool.query<{ original_url: string }>(
    `SELECT original_url FROM videos WHERE session_id IN (${placeholders})`,
    sessionIds,
  );

  const blobUrls = [
    ...saRes.rows.map(r => r.blob_url),
    ...outRes.rows.map(r => r.blob_url),
    ...kfRes.rows.map(r => r.frame_url),
    ...videoRes.rows.map(r => r.original_url),
  ].filter(Boolean);

  const blobPrefixes = sessionIds.map(id => `generated/${id}/`);

  return { sessionIds, blobUrls, blobPrefixes };
}

export async function deleteTestSessions(userId: string): Promise<number> {
  const pool = getPool();
  // Delete jobs linked to test sessions (cascades job_logs, job_steps, outputs)
  await pool.query(
    `DELETE FROM jobs WHERE session_id IN (SELECT id FROM sessions WHERE user_id = $1 AND is_test = TRUE)`,
    [userId],
  );
  // Delete videos linked to test sessions (cascades video_keyframe_index)
  await pool.query(
    `DELETE FROM videos WHERE session_id IN (SELECT id FROM sessions WHERE user_id = $1 AND is_test = TRUE)`,
    [userId],
  );
  // Delete sessions (cascades session_assets)
  const result = await pool.query(
    `DELETE FROM sessions WHERE user_id = $1 AND is_test = TRUE`,
    [userId],
  );
  return result.rowCount ?? 0;
}

export async function listAllSessionBlobs(userId: string): Promise<{
  sessionIds: string[];
  blobUrls: string[];
  blobPrefixes: string[];
}> {
  const pool = getPool();

  const sessionsRes = await pool.query<{ id: string }>(
    `SELECT id FROM sessions WHERE user_id = $1`,
    [userId],
  );
  const sessionIds = sessionsRes.rows.map(r => r.id);

  const saUrls: string[] = [];
  if (sessionIds.length > 0) {
    const placeholders = sessionIds.map((_, i) => `$${i + 1}`).join(', ');
    const saRes = await pool.query<{ blob_url: string }>(
      `SELECT blob_url FROM session_assets WHERE session_id IN (${placeholders})`,
      sessionIds,
    );
    saUrls.push(...saRes.rows.map(r => r.blob_url));
  }

  // Query outputs and keyframes by user_id to catch sessionless jobs/videos
  const outRes = await pool.query<{ blob_url: string }>(
    `SELECT blob_url FROM outputs
     WHERE job_id IN (SELECT id FROM jobs WHERE user_id = $1)`,
    [userId],
  );
  const kfRes = await pool.query<{ frame_url: string }>(
    `SELECT frame_url FROM video_keyframe_index
     WHERE video_id IN (SELECT id FROM videos WHERE user_id = $1)`,
    [userId],
  );
  const videoRes = await pool.query<{ original_url: string }>(
    `SELECT original_url FROM videos WHERE user_id = $1`,
    [userId],
  );

  const blobUrls = [
    ...saUrls,
    ...outRes.rows.map(r => r.blob_url),
    ...kfRes.rows.map(r => r.frame_url),
    ...videoRes.rows.map(r => r.original_url),
  ].filter(Boolean);

  const blobPrefixes = sessionIds.map(id => `generated/${id}/`);

  return { sessionIds, blobUrls, blobPrefixes };
}

export async function deleteAllSessions(userId: string): Promise<number> {
  const pool = getPool();
  // Delete jobs by user_id (cascades job_logs, job_steps, outputs)
  const jobsResult = await pool.query(`DELETE FROM jobs WHERE user_id = $1`, [userId]);
  // Delete videos by user_id (cascades video_keyframe_index)
  await pool.query(`DELETE FROM videos WHERE user_id = $1`, [userId]);
  // Delete sessions by user_id (cascades session_assets)
  const sessionsResult = await pool.query(`DELETE FROM sessions WHERE user_id = $1`, [userId]);
  return (sessionsResult.rowCount ?? 0) + (jobsResult.rowCount ?? 0);
}

// ─── Videos ───────────────────────────────────────────────────────────────────

export async function createVideoRecord(params: {
  id: string;
  userId: string;
  originalUrl: string;
  sessionId?: string;
}): Promise<void> {
  const pool = getPool();
  await pool.query(
    `INSERT INTO videos (id, user_id, original_url, session_id, status)
     VALUES ($1, $2, $3, $4, 'uploaded')`,
    [params.id, params.userId, params.originalUrl, params.sessionId ?? null]
  );
}

export async function getVideoStatus(videoId: string, userId: string): Promise<string | null> {
  const pool = getPool();
  const result = await pool.query<{ status: string }>(
    'SELECT status FROM videos WHERE id = $1 AND user_id = $2',
    [videoId, userId]
  );
  return result.rows[0]?.status ?? null;
}

// ─── Assets ───────────────────────────────────────────────────────────────────

export async function createAssetRecord(params: {
  id: string;
  userId: string;
  sessionId?: string;
  blobUrl: string;
  filename: string;
  contentType: string;
  fileSizeBytes?: number;
}): Promise<Asset> {
  const pool = getPool();
  const result = await pool.query<Asset>(
    `INSERT INTO assets (id, user_id, session_id, blob_url, filename, content_type, file_size_bytes, source)
     VALUES ($1, $2, $3, $4, $5, $6, $7, 'upload')
     RETURNING *`,
    [
      params.id,
      params.userId,
      params.sessionId ?? null,
      params.blobUrl,
      params.filename,
      params.contentType,
      params.fileSizeBytes ?? null,
    ]
  );
  return result.rows[0];
}

export async function getAssetById(assetId: string, userId: string): Promise<Asset | null> {
  const pool = getPool();
  const result = await pool.query<Asset>(
    'SELECT * FROM assets WHERE id = $1 AND user_id = $2',
    [assetId, userId]
  );
  return result.rows[0] ?? null;
}

// ─── Outputs ──────────────────────────────────────────────────────────────────

export async function getJobOutputs(jobId: string): Promise<Output[]> {
  const pool = getPool();
  const result = await pool.query<Output>(
    'SELECT * FROM outputs WHERE job_id = $1 ORDER BY created_at',
    [jobId]
  );
  return result.rows;
}

// ─── Session assets ───────────────────────────────────────────────────────────

export async function createSessionAssetRecord(params: {
  sessionId: string;
  assetType: string;
  blobUrl: string;
  filename?: string;
  contentType?: string;
  sourceId?: string;
  label?: string;
  metadataJson?: object;
}): Promise<SessionAsset> {
  const pool = getPool();
  const result = await pool.query<SessionAsset>(
    `INSERT INTO session_assets
       (session_id, asset_type, blob_url, filename, content_type, source_id, label, metadata_json)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
     RETURNING *`,
    [
      params.sessionId,
      params.assetType,
      params.blobUrl,
      params.filename ?? null,
      params.contentType ?? null,
      params.sourceId ?? null,
      params.label ?? null,
      params.metadataJson ? JSON.stringify(params.metadataJson) : null,
    ]
  );
  return result.rows[0];
}

export async function getSessionAssets(sessionId: string): Promise<SessionAsset[]> {
  const pool = getPool();
  const result = await pool.query<SessionAsset>(
    'SELECT * FROM session_assets WHERE session_id = $1 AND session_hidden = false ORDER BY created_at',
    [sessionId]
  );
  return result.rows;
}

/**
 * Fetch a single session_asset by id, verifying ownership via sessions.user_id.
 * Returns null if not found or not owned by userId.
 */
export async function getSessionAssetById(
  id: string,
  userId: string
): Promise<SessionAsset | null> {
  const pool = getPool();
  const result = await pool.query<SessionAsset>(
    `SELECT sa.*
     FROM session_assets sa
     JOIN sessions s ON s.id = sa.session_id
     WHERE sa.id = $1 AND s.user_id = $2`,
    [id, userId]
  );
  return result.rows[0] ?? null;
}

// ─── Job steps ────────────────────────────────────────────────────────────────

export interface JobStep {
  id: string;
  job_id: string;
  step_name: string;
  status: string;
  result: object | null;
  created_at: Date;
}

export async function getJobStepsSince(jobId: string, afterDate: Date): Promise<JobStep[]> {
  const pool = getPool();
  const result = await pool.query<JobStep>(
    `SELECT id, job_id, step_name, status, result, created_at
     FROM job_steps
     WHERE job_id = $1 AND created_at > $2
     ORDER BY created_at ASC`,
    [jobId, afterDate],
  );
  return result.rows;
}

export async function getJobSteps(jobId: string): Promise<JobStep[]> {
  const pool = getPool();
  const result = await pool.query<JobStep>(
    `SELECT id, job_id, step_name, status, result, created_at
     FROM job_steps
     WHERE job_id = $1
     ORDER BY created_at ASC`,
    [jobId],
  );
  return result.rows;
}

// ─── Job logs ─────────────────────────────────────────────────────────────────

export async function getJobLogs(jobId: string): Promise<JobLog[]> {
  const pool = getPool();
  const result = await pool.query<JobLog>(
    `SELECT id, job_id, session_id, service_name, log_type, model_id,
            tool_name, agent_role, task_name, message, message_type,
            call_group_id, sequence_num, error_text, cached, created_at
     FROM job_logs
     WHERE job_id = $1
     ORDER BY sequence_num ASC`,
    [jobId]
  );
  return result.rows;
}

export async function getJobLogsSince(jobId: string, afterSeq: number): Promise<JobLog[]> {
  const pool = getPool();
  const result = await pool.query<JobLog>(
    `SELECT id, job_id, session_id, service_name, log_type, model_id,
            tool_name, agent_role, task_name, message, message_type,
            call_group_id, sequence_num, error_text, cached, created_at
     FROM job_logs
     WHERE job_id = $1 AND sequence_num > $2
     ORDER BY sequence_num ASC`,
    [jobId, afterSeq]
  );
  return result.rows;
}

/**
 * Returns tool_progress rows updated after afterDate, plus all rows with
 * status='running' so the frontend always sees active progress bars even on
 * reconnect.
 */
export async function getToolProgressSince(
  jobId: string,
  afterDate: Date,
): Promise<ToolProgress[]> {
  const pool = getPool();
  const result = await pool.query<ToolProgress>(
    `SELECT call_group_id, job_id, tool_name, total_units, processed_units,
            unit_label, status, started_at, updated_at
     FROM tool_progress
     WHERE job_id = $1
       AND (updated_at > $2 OR status = 'running')
     ORDER BY started_at ASC`,
    [jobId, afterDate],
  );
  return result.rows;
}

// ─── Session history ──────────────────────────────────────────────────────────

/**
 * Delete all conversation history for a session (jobs, steps, logs, outputs,
 * tool progress, and job-output session_assets) while keeping uploaded files,
 * keyframes, assets, and the tool-call cache.
 */
export async function clearSessionHistory(sessionId: string): Promise<void> {
  const pool = getPool();
  // Hide analysis assets so the planner starts fresh; rows remain for cache-hit re-exposure
  await pool.query(
    `UPDATE session_assets
        SET session_hidden = true
      WHERE session_id = $1
        AND asset_type = 'job_analysis_result'`,
    [sessionId],
  );
  // Remove output/segment session_assets before deleting jobs so the
  // source_job_id FK doesn't silently go NULL on remaining rows.
  await pool.query(
    `DELETE FROM session_assets
      WHERE session_id = $1
        AND asset_type IN ('job_output_video', 'job_output_file', 'segment')`,
    [sessionId],
  );
  // Cascades: job_steps, job_logs, outputs, tool_progress
  await pool.query('DELETE FROM jobs WHERE session_id = $1', [sessionId]);
}

// ─── Tool call cache ──────────────────────────────────────────────────────────

/** Delete all cached tool call results for a user. Returns the row count deleted. */
export async function clearToolCache(userId: string): Promise<number> {
  const pool = getPool();
  const result = await pool.query(
    'DELETE FROM tool_call_cache WHERE user_id = $1',
    [userId],
  );
  return result.rowCount ?? 0;
}
