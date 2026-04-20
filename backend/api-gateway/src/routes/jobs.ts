import { Router, Request, Response } from 'express';
import { v4 as uuidv4 } from 'uuid';
import { createJob, getJobById, getJobOutputs, listJobsForUser, getJobLogs, getJobLogsSince, getJobStepsSince, getJobSteps, ensureSessionExists, getToolProgressSince } from '../services/dbService';
import { publishJobQueued } from '../services/serviceBusService';
import { generateSignedDownloadUrl } from '../services/blobService';

export const jobsRouter = Router();

jobsRouter.get('/', async (req, res, next) => {
  try {
    const rawFilter = req.query.filter as string | undefined;
    const filter = (['real', 'test', 'all'].includes(rawFilter ?? '')
      ? rawFilter : 'real') as 'real' | 'test' | 'all';
    const jobs = await listJobsForUser(req.user!.id, filter);
    const signed = jobs.map(j => ({
      ...j,
      output_url: j.output_url ? generateSignedDownloadUrl(j.output_url) : null,
    }));
    res.json({ jobs: signed });
  } catch (err) {
    next(err);
  }
});

jobsRouter.post('/draft', async (req, res, next) => {
  try {
    const { sessionId } = req.body as { sessionId?: string };
    // Recreate the session row if it was deleted (e.g. DB reset) to avoid FK violations.
    if (sessionId) {
      await ensureSessionExists(req.user!.id, sessionId);
    }
    const job = await createJob({
      id: uuidv4(),
      userId: req.user!.id,
      prompt: '',
      status: 'draft',
      sessionId,
    });
    res.status(201).json({ job });
  } catch (err) {
    next(err);
  }
});

jobsRouter.post('/', async (req, res, next) => {
  try {
    const {
      videoId,
      videoIds,
      prompt,
      sessionId,
      parentJobId,
    } = req.body as {
      videoId?: string;
      videoIds?: string[];
      prompt: string;
      sessionId?: string;
      parentJobId?: string;
    };

    // Accept either videoId (legacy) or videoIds (new); require at least one
    const resolvedVideoIds = videoIds ?? (videoId ? [videoId] : []);
    const resolvedVideoId = resolvedVideoIds[0] ?? '';

    if (!resolvedVideoId || !prompt) {
      res.status(400).json({ error: 'videoId (or videoIds) and prompt are required' });
      return;
    }

    const userId = req.user!.id;
    const jobId = uuidv4();

    const job = await createJob({
      id: jobId,
      userId,
      videoId: resolvedVideoId,
      videoIds: resolvedVideoIds,
      prompt,
      sessionId,
      parentJobId,
    });

    await publishJobQueued({
      jobId,
      userId,
      videoId: resolvedVideoId,
      videoIds: resolvedVideoIds,
      prompt,
      sessionId,
      parentJobId,
    });

    res.status(201).json({ jobId: job.id, status: job.status });
  } catch (err) {
    next(err);
  }
});

jobsRouter.get('/:id', async (req, res, next) => {
  try {
    const job = await getJobById(req.params.id);
    if (!job) {
      res.status(404).json({ error: 'Job not found' });
      return;
    }
    res.json(job);
  } catch (err) {
    next(err);
  }
});

jobsRouter.get('/:id/outputs', async (req, res, next) => {
  try {
    const outputs = await getJobOutputs(req.params.id);
    const withSignedUrls = outputs.map(o => ({
      ...o,
      signed_url: generateSignedDownloadUrl(o.blob_url),
    }));
    res.json({ outputs: withSignedUrls });
  } catch (err) {
    next(err);
  }
});

jobsRouter.get('/:id/steps', async (req, res, next) => {
  try {
    const steps = await getJobSteps(req.params.id);
    res.json({ steps });
  } catch (err) {
    next(err);
  }
});

jobsRouter.get('/:id/logs', async (req, res, next) => {
  try {
    const logs = await getJobLogs(req.params.id);
    res.json({ logs });
  } catch (err) {
    next(err);
  }
});

jobsRouter.get('/:id/stream', async (req: Request, res: Response) => {
  const jobId = req.params.id;

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  const sendEvent = (data: object) => {
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  // Track the last step timestamp, last log sequence_num, and last progress date we emitted.
  let lastStepDate = new Date(0);
  let lastLogSeq = -1;
  let lastProgressDate = new Date(0);

  const poll = setInterval(async () => {
    try {
      // Emit any new job_steps rows since the last poll tick.
      const steps = await getJobStepsSince(jobId, lastStepDate);
      for (const step of steps) {
        sendEvent({ type: 'progress', jobId, stepName: step.step_name, stepStatus: step.status });
        lastStepDate = step.created_at;
      }

      // Emit any new job_logs rows written since the last poll tick.
      const logs = await getJobLogsSince(jobId, lastLogSeq);
      for (const log of logs) {
        sendEvent({ type: 'log', jobId, log });
        if ((log.sequence_num as number) > lastLogSeq) {
          lastLogSeq = log.sequence_num as number;
        }
      }

      // Emit tool_progress updates (always includes running rows for reconnect resilience).
      const toolProgressRows = await getToolProgressSince(jobId, lastProgressDate);
      for (const tp of toolProgressRows) {
        sendEvent({ type: 'tool_progress', jobId, toolProgress: tp });
        if (tp.updated_at > lastProgressDate) {
          lastProgressDate = tp.updated_at;
        }
      }

      const job = await getJobById(jobId);
      if (!job) {
        sendEvent({ type: 'status', error: 'Job not found' });
        clearInterval(poll);
        res.end();
        return;
      }

      sendEvent({ type: 'status', jobId: job.id, status: job.status, outputUrl: job.output_url });

      if (job.status === 'completed' || job.status === 'failed') {
        clearInterval(poll);
        res.end();
      }
    } catch (err) {
      console.error('SSE poll error:', err);
      clearInterval(poll);
      res.end();
    }
  }, 2000);

  req.on('close', () => {
    clearInterval(poll);
  });
});
