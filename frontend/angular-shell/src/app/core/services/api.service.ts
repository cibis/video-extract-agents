import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface Job {
  id: string;
  user_id: string;
  session_id: string | null;
  video_id: string;
  prompt: string;
  status: 'draft' | 'queued' | 'processing' | 'completed' | 'failed';
  output_url: string | null;
  error: string | null;
  is_test: boolean;
  created_at: string;
  updated_at: string;
}

export interface Output {
  id: string;
  job_id: string;
  session_id: string | null;
  blob_url: string;
  filename: string | null;
  content_type: string;
  signed_url: string;
  created_at: string;
}

export interface LibrechatTokens {
  token: string;
  refreshToken: string;
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
  signed_url: string;
  created_at: string;
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
  created_at: string;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  /** @deprecated Use requestVideoSasUrl(sessionId) for session-aware uploads */
  requestSasUrl(): Observable<{ videoId: string; uploadUrl: string; blobPath: string }> {
    return this.http.post<{ videoId: string; uploadUrl: string; blobPath: string }>(
      `${this.base}/v1/videos`,
      {}
    );
  }

  requestVideoSasUrl(sessionId: string, filename?: string): Observable<{ videoId: string; uploadUrl: string; blobPath: string }> {
    return this.http.post<{ videoId: string; uploadUrl: string; blobPath: string }>(
      `${this.base}/v1/videos`,
      { sessionId, ...(filename ? { filename } : {}) }
    );
  }

  requestAssetSasUrl(params: {
    sessionId: string;
    filename: string;
    contentType: string;
  }): Observable<{ assetId: string; uploadUrl: string; blobPath: string }> {
    return this.http.post<{ assetId: string; uploadUrl: string; blobPath: string }>(
      `${this.base}/v1/assets`,
      params
    );
  }

  createSession(): Observable<{ sessionId: string }> {
    return this.http.post<{ sessionId: string }>(`${this.base}/v1/sessions`, {});
  }

  getSessionAssets(sessionId: string): Observable<{ assets: SessionAsset[] }> {
    return this.http.get<{ assets: SessionAsset[] }>(`${this.base}/v1/sessions/${sessionId}/assets`);
  }

  restartSession(sessionId: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(
      `${this.base}/v1/sessions/${sessionId}/restart`, {}
    );
  }

  getJobSteps(jobId: string): Observable<{ steps: { step_name: string; status: string }[] }> {
    return this.http.get<{ steps: { step_name: string; status: string }[] }>(`${this.base}/v1/jobs/${jobId}/steps`);
  }

  getJobLogs(jobId: string): Observable<{ logs: JobLog[] }> {
    return this.http.get<{ logs: JobLog[] }>(`${this.base}/v1/jobs/${jobId}/logs`);
  }

  /** @deprecated Use createJobMulti for session-aware multi-video jobs */
  createJob(videoId: string, prompt: string): Observable<{ jobId: string; status: string }> {
    return this.http.post<{ jobId: string; status: string }>(
      `${this.base}/v1/jobs`,
      { videoId, prompt }
    );
  }

  createJobMulti(params: {
    videoIds: string[];
    prompt: string;
    sessionId: string;
    parentJobId?: string;
  }): Observable<{ jobId: string; status: string }> {
    return this.http.post<{ jobId: string; status: string }>(
      `${this.base}/v1/jobs`,
      {
        videoIds: params.videoIds,
        prompt: params.prompt,
        sessionId: params.sessionId,
        ...(params.parentJobId ? { parentJobId: params.parentJobId } : {}),
      }
    );
  }

  getJob(jobId: string): Observable<Job> {
    return this.http.get<Job>(`${this.base}/v1/jobs/${jobId}`);
  }

  listJobs(filter: 'real' | 'test' | 'all' = 'real'): Observable<{ jobs: Job[] }> {
    return this.http.get<{ jobs: Job[] }>(`${this.base}/v1/jobs?filter=${filter}`);
  }

  wipeTestData(): Observable<{ sessionsDeleted: number; blobsDeleted: number }> {
    return this.http.delete<{ sessionsDeleted: number; blobsDeleted: number }>(
      `${this.base}/v1/admin/wipe-test-data`
    );
  }

  wipeAllData(): Observable<{ sessionsDeleted: number; blobsDeleted: number }> {
    return this.http.delete<{ sessionsDeleted: number; blobsDeleted: number }>(
      `${this.base}/v1/admin/wipe-all-data`
    );
  }

  createDraftJob(sessionId?: string | null): Observable<{ job: Job }> {
    return this.http.post<{ job: Job }>(`${this.base}/v1/jobs/draft`, { sessionId: sessionId ?? null });
  }

  getJobOutputs(jobId: string): Observable<{ outputs: Output[] }> {
    return this.http.get<{ outputs: Output[] }>(`${this.base}/v1/jobs/${jobId}/outputs`);
  }

  getOutput(jobId: string): Observable<{ outputUrl: string }> {
    return this.http.get<{ outputUrl: string }>(`${this.base}/v1/outputs/${jobId}`);
  }

  /**
   * Provisions a LibreChat user for the currently authenticated Entra user
   * and returns a LibreChat session token pair for iframe bootstrap.
   *
   * Strategy:
   *   1. Decode the Entra JWT client-side to extract the user's email and sub.
   *   2. Derive a stable, deterministic LibreChat password using HMAC-SHA256
   *      of the Entra sub against a public salt.  The password never leaves
   *      the browser and is not stored anywhere.
   *   3. POST /api/auth/register to LibreChat — ignored if the user already
   *      exists (400/409).
   *   4. POST /api/auth/login to LibreChat — returns { token, refreshToken }.
   *
   * In local dev (no real Entra JWT) fixed dev@local credentials are used so
   * developers get a seamless experience without the full Entra flow.
   *
   * Uses native fetch (not Angular HttpClient) to bypass the auth interceptor —
   * LibreChat's own auth endpoints must not receive the Entra JWT header.
   */
  async provisionLibrechatUser(entraToken: string | null): Promise<LibrechatTokens> {
    const librechatBase = environment.librechatUrl;

    let email: string;
    let sub: string;

    if (entraToken) {
      const claims = decodeJwtPayload(entraToken);
      email = claims['email'] ?? claims['preferred_username'] ?? '';
      sub = claims['oid'] ?? claims['sub'] ?? email;
    } else {
      // Local dev: no real Entra session.
      // Must be a valid email format — LibreChat rejects single-label domains.
      email = 'dev@local.dev';
      sub = 'local-dev-user';
    }

    const password = await deriveLibrechatPassword(sub);

    const doLogin = () => fetch(`${librechatBase}/api/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });

    // Try login first — skips registration entirely for returning users.
    let loginRes = await doLogin();

    if (!loginRes.ok) {
      // User doesn't exist yet (or password mismatch from a stale record).
      // Register then retry login once.
      await fetch(`${librechatBase}/api/auth/register`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: email.split('@')[0],
          email,
          password,
          confirm_password: password,
        }),
      }).catch(() => { /* ignore — login below will surface real failures */ });

      loginRes = await doLogin();
    }

    if (!loginRes.ok) {
      throw new Error(`LibreChat login failed: ${loginRes.status}`);
    }

    const body = await loginRes.json() as { token?: string; refreshToken?: string };
    if (!body.token) {
      throw new Error('LibreChat login response missing token');
    }

    return {
      token: body.token,
      refreshToken: body.refreshToken ?? '',
    };
  }
}

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Decodes a JWT payload without verifying the signature. */
function decodeJwtPayload(token: string): Record<string, string> {
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json) as Record<string, string>;
  } catch {
    return {};
  }
}

/**
 * Derives a deterministic LibreChat password for a given Entra sub/oid.
 * Uses HMAC-SHA256 with a fixed salt so the result is stable across sessions
 * but not simply the sub value itself.
 */
async function deriveLibrechatPassword(sub: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode('lc-sso-salt-v1'),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(sub));
  return Array.from(new Uint8Array(sig))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, 32);
}
