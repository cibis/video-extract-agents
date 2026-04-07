import { Pool } from 'pg';
import { config } from '../config';

let _pool: Pool | null = null;

export function getPool(): Pool {
  if (!_pool) {
    _pool = new Pool({ connectionString: config.DATABASE_URL });
  }
  return _pool;
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
  return (result as any).rowCount ?? 0;
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
  return ((sessionsResult as any).rowCount ?? 0) + ((jobsResult as any).rowCount ?? 0);
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
    'SELECT * FROM session_assets WHERE session_id = $1 ORDER BY created_at',
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
            call_group_id, sequence_num, error_text, created_at
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
            call_group_id, sequence_num, error_text, created_at
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
