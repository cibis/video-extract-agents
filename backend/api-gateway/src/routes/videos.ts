import { Router } from 'express';
import { v4 as uuidv4 } from 'uuid';
import { generateSasUploadUrl, getInternalBlobUrl } from '../services/blobService';
import { createVideoRecord, ensureSessionExists, createSessionAssetRecord, getVideoStatus } from '../services/dbService';
import { publishVideoUploaded } from '../services/serviceBusService';
import { registerPendingUpload } from '../services/pendingUploads';
import { config } from '../config';

export const videosRouter = Router();

videosRouter.post('/', async (req, res, next) => {
  try {
    const userId = req.user!.id;
    const { sessionId, filename } = req.body as { sessionId?: string; filename?: string };
    const videoId = uuidv4();

    const { sasUrl, blobPath } = await generateSasUploadUrl(userId, videoId, filename);

    // Internal blob URL (used by backend services to read the video)
    const blobUrl = getInternalBlobUrl(blobPath);

    // Upload URL returned to the browser
    const uploadUrl = config.OUTPUT_URL_MODE === 'local'
      ? `${config.BLOB_PROXY_BASE_URL}/v1/blob-proxy/${blobPath}`
      : sasUrl;

    if (sessionId) await ensureSessionExists(userId, sessionId);
    await createVideoRecord({ id: videoId, userId, originalUrl: blobUrl, sessionId });

    // Create session_asset immediately so the file list survives a page refresh
    // without waiting for the preprocessing worker to complete.
    if (sessionId) {
      await createSessionAssetRecord({
        sessionId,
        assetType: 'uploaded_video',
        blobUrl,
        filename: filename ?? undefined,
        contentType: 'video/*',
        sourceId: videoId,
      });
    }

    if (config.OUTPUT_URL_MODE === 'local') {
      // In local dev the client uploads bytes to the blob-proxy AFTER this
      // response is returned. Defer the VIDEO_UPLOADED event until the
      // blob-proxy PUT handler confirms the file has been written to Azurite.
      registerPendingUpload(blobPath, { videoId, userId, blobUrl, sessionId });
    } else {
      await publishVideoUploaded({ videoId, userId, blobUrl, sessionId });
    }

    res.status(201).json({ videoId, uploadUrl, blobPath });
  } catch (err) {
    next(err);
  }
});

videosRouter.get('/:id/status', async (req, res, next) => {
  try {
    const userId = req.user!.id;
    const status = await getVideoStatus(req.params.id, userId);
    if (status === null) return res.status(404).json({ error: 'Not found' });
    res.json({ status });
  } catch (err) {
    next(err);
  }
});
