import { Router } from 'express';
import { v4 as uuidv4 } from 'uuid';
import { createAssetRecord, createSessionAssetRecord, getAssetById } from '../services/dbService';
import { generateSasUploadUrl, generateSignedDownloadUrl, getInternalBlobUrl } from '../services/blobService';
import { config } from '../config';

export const assetsRouter = Router();

assetsRouter.post('/', async (req, res, next) => {
  try {
    const userId = req.user!.id;
    const { sessionId, filename, contentType } = req.body as {
      sessionId?: string;
      filename: string;
      contentType: string;
    };

    if (!filename || !contentType) {
      res.status(400).json({ error: 'filename and contentType are required' });
      return;
    }

    const assetId = uuidv4();
    const blobPath = `${userId}/assets/${assetId}/${filename}`;

    const { sasUrl } = await generateSasUploadUrl(userId, `assets/${assetId}/${filename}`);

    // Internal blob URL used by backend services to read the asset
    const blobUrl = getInternalBlobUrl(blobPath);

    // Upload URL returned to the browser — blob-proxy in local dev, SAS URL in CI/prod
    const uploadUrl = config.OUTPUT_URL_MODE === 'local'
      ? `${config.BLOB_PROXY_BASE_URL}/v1/blob-proxy/${blobPath}`
      : sasUrl;

    await createAssetRecord({
      id: assetId,
      userId,
      sessionId,
      blobUrl,
      filename,
      contentType,
    });

    if (sessionId) {
      await createSessionAssetRecord({
        sessionId,
        assetType: 'uploaded_file',
        blobUrl,
        filename,
        contentType,
        sourceId: assetId,
      });
    }

    res.status(201).json({ assetId, uploadUrl, blobPath });
  } catch (err) {
    next(err);
  }
});

assetsRouter.get('/:id', async (req, res, next) => {
  try {
    const userId = req.user!.id;
    const asset = await getAssetById(req.params.id, userId);
    if (!asset) {
      res.status(404).json({ error: 'Asset not found' });
      return;
    }
    res.json({
      ...asset,
      signed_url: generateSignedDownloadUrl(asset.blob_url),
    });
  } catch (err) {
    next(err);
  }
});
