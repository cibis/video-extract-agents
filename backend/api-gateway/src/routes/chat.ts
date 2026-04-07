import { Router } from 'express';
import { v4 as uuidv4 } from 'uuid';
import axios from 'axios';
import { config } from '../config';
import { getLatestSessionForUser, createJob, updateJobPrompt, findActiveJobForSession, findDraftJobWithoutSession } from '../services/dbService';
import { generateSignedDownloadUrl } from '../services/blobService';

export const chatRouter = Router();

/**
 * Start an OpenAI-compatible SSE response immediately, flushing the x-job-id
 * header and an empty role chunk to the browser right away.
 *
 * This ensures patchFetch in LibreChat's JobStatusBridge sees x-job-id as soon
 * as the job starts (not after it finishes), so the Angular shell can open the
 * SSE progress stream before the orchestrator returns.
 *
 * Returns the chunkId so the caller can reuse it for subsequent content chunks.
 */
function startStream(res: import('express').Response, jobId?: string): string {
  const chunkId = `chatcmpl-${Date.now()}`;

  if (jobId) res.setHeader('x-job-id', jobId);
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  // Write the role chunk immediately — this flushes headers to the browser so
  // fetch() resolves and patchFetch can read x-job-id without waiting for the body.
  res.write(`data: ${JSON.stringify({
    id: chunkId,
    object: 'chat.completion.chunk',
    created: Math.floor(Date.now() / 1000),
    model: 'video-extract-agent',
    choices: [{ index: 0, delta: { role: 'assistant', content: '' }, finish_reason: null }],
  })}\n\n`);

  return chunkId;
}

/** Write a content delta + stop chunk + [DONE] and end the response. */
function finishStream(res: import('express').Response, chunkId: string, text: string): void {
  res.write(`data: ${JSON.stringify({
    id: chunkId,
    object: 'chat.completion.chunk',
    created: Math.floor(Date.now() / 1000),
    model: 'video-extract-agent',
    choices: [{ index: 0, delta: { content: text }, finish_reason: null }],
  })}\n\n`);

  res.write(`data: ${JSON.stringify({
    id: chunkId,
    object: 'chat.completion.chunk',
    created: Math.floor(Date.now() / 1000),
    model: 'video-extract-agent',
    choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
  })}\n\n`);

  res.write('data: [DONE]\n\n');
  res.end();
}

chatRouter.post('/', async (req, res) => {
  let jobId: string | undefined;
  try {
    const upstream = `${config.AGENT_ORCHESTRATOR_URL}/run`;

    const forwardHeaders: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-User-Id': req.user!.id,
      'X-User-Email': req.user!.email,
    };
    const parentJobId = req.headers['x-parent-job-id'];
    if (parentJobId) forwardHeaders['X-Parent-Job-Id'] = String(parentJobId);

    // Use X-Session-Id from the request header if provided; otherwise fall back to
    // the user's most recent session.  LibreChat's backend proxies the chat request
    // server-side, so the browser-injected header from JobStatusBridge never arrives
    // here — the auto-lookup ensures video context is always attached.
    let sessionId = req.headers['x-session-id'] ? String(req.headers['x-session-id']) : null;
    if (!sessionId) {
      const latestSession = await getLatestSessionForUser(req.user!.id);
      if (latestSession) sessionId = latestSession.id;
    }
    if (sessionId) forwardHeaders['X-Session-Id'] = sessionId;

    // LibreChat sends an OpenAI-format body: { messages: [{role, content}], model, ... }
    // The orchestrator expects: { prompt: string, ... }
    // Extract the last user message as the prompt.
    const body = req.body as { messages?: { role: string; content: string }[]; prompt?: string };
    let prompt = body.prompt ?? '';
    if (!prompt && Array.isArray(body.messages)) {
      const userMessages = body.messages.filter((m) => m.role === 'user');
      prompt = userMessages[userMessages.length - 1]?.content ?? '';
    }

    // preCreatedJobId is injected by JobStatusBridge when the browser calls our API directly.
    // For LibreChat custom endpoints, the call is server-side so this will be undefined.
    const preCreatedJobId = (body as Record<string, unknown>).jobId as string | undefined;

    if (preCreatedJobId) {
      await updateJobPrompt(preCreatedJobId, req.user!.id, prompt, sessionId ?? null);
      jobId = preCreatedJobId;
    } else if (sessionId) {
      // Server-side call: find the pre-created draft (or already-active) job for this session
      // to avoid creating a duplicate on every LibreChat request / retry.
      // Fall back to a session-less draft (created before the user's first upload) so that
      // the pre-created draft is claimed and linked to the session rather than discarded.
      let activeJob = await findActiveJobForSession(req.user!.id, sessionId);
      if (!activeJob) {
        activeJob = await findDraftJobWithoutSession(req.user!.id);
      }
      if (activeJob) {
        if (activeJob.status === 'draft') {
          await updateJobPrompt(activeJob.id, req.user!.id, prompt, sessionId);
        }
        jobId = activeJob.id;
      } else {
        jobId = uuidv4();
        await createJob({ id: jobId, userId: req.user!.id, videoId: null, prompt, sessionId });
      }
    } else {
      jobId = uuidv4();
      await createJob({
        id: jobId,
        userId: req.user!.id,
        videoId: null,
        prompt,
        sessionId: undefined,
      });
    }

    const orchestratorBody = { prompt, job_id: jobId };

    // Flush headers + role chunk immediately so patchFetch sees x-job-id before
    // the orchestrator returns. fetch() resolves as soon as headers arrive, so
    // the Angular shell can open the SSE progress stream right away.
    const chunkId = startStream(res, jobId);

    // Send SSE keepalive comments every 15 s while the orchestrator runs.
    // LibreChat's OpenAI HTTP client idles out after ~5 min with no data and
    // closes the stream before the job completes. SSE comment lines (": ...\n\n")
    // are ignored by the OpenAI SSE parser but keep the TCP connection alive.
    const keepalive = setInterval(() => {
      if (!res.writableEnded) res.write(': keepalive\n\n');
    }, 15_000);

    try {
      const response = await axios.post(upstream, orchestratorBody, {
        headers: forwardHeaders,
        responseType: 'json',
        timeout: 0, // no timeout — agent runs can take arbitrarily long
      });

      const data = response.data as { output_url?: string };

      // Convert internal blob URL to browser-accessible signed/proxy URL
      const downloadUrl = data.output_url
        ? generateSignedDownloadUrl(data.output_url)
        : null;

      const replyText = downloadUrl
        ? `Job complete. [Download output](${downloadUrl})`
        : 'Job complete.';

      finishStream(res, chunkId, replyText);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An error occurred. Please try again.';
      if (!res.writableEnded) {
        finishStream(res, chunkId, `Error: ${message}`);
      } else {
        res.end();
      }
    } finally {
      clearInterval(keepalive);
    }
  } catch (err) {
    // Errors before startStream (e.g. DB failure during job setup)
    const message = err instanceof Error ? err.message : 'An error occurred. Please try again.';
    if (!res.headersSent) {
      const chunkId = startStream(res, jobId);
      finishStream(res, chunkId, `Error: ${message}`);
    } else if (!res.writableEnded) {
      res.end();
    }
  }
});
