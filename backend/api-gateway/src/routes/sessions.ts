import { Router } from 'express';
import { createSession, getSessionAssets } from '../services/dbService';
import { generateSignedDownloadUrl } from '../services/blobService';

export const sessionsRouter = Router();

sessionsRouter.post('/', async (req, res, next) => {
  try {
    const { isTest } = req.body as { isTest?: boolean };
    const session = await createSession(req.user!.id, isTest ?? false);
    res.status(201).json({ sessionId: session.id });
  } catch (err) {
    next(err);
  }
});

sessionsRouter.get('/:id/assets', async (req, res, next) => {
  try {
    const sessionId = req.params.id;
    const assets = await getSessionAssets(sessionId);
    const withSignedUrls = assets.map(asset => ({
      ...asset,
      signed_url: generateSignedDownloadUrl(asset.blob_url),
    }));
    res.json({ assets: withSignedUrls });
  } catch (err) {
    next(err);
  }
});
