import { Router } from 'express';
import { getJobById } from '../services/dbService';
import { generateSignedDownloadUrl } from '../services/blobService';

export const outputsRouter = Router();

outputsRouter.get('/:id', async (req, res, next) => {
  try {
    const job = await getJobById(req.params.id);
    if (!job) {
      res.status(404).json({ error: 'Output not found' });
      return;
    }

    if (job.status !== 'completed' || !job.output_url) {
      res.status(404).json({ error: 'Output not yet available', status: job.status });
      return;
    }

    const outputUrl = generateSignedDownloadUrl(job.output_url);
    res.json({ outputUrl });
  } catch (err) {
    next(err);
  }
});
