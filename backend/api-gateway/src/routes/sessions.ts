import { Router } from 'express';
import { createSession, getSessionAssets, clearSessionHistory } from '../services/dbService';
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
    const withSignedUrls = await Promise.all(assets.map(async asset => ({
      ...asset,
      signed_url: await generateSignedDownloadUrl(asset.blob_url),
    })));
    res.json({ assets: withSignedUrls });
  } catch (err) {
    next(err);
  }
});

/** POST /v1/sessions/:id/restart — wipe conversation history, keep uploads */
sessionsRouter.post('/:id/restart', async (req, res, next) => {
  try {
    await clearSessionHistory(req.params.id);
    res.json({ ok: true });
  } catch (err) {
    next(err);
  }
});
