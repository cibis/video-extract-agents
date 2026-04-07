import { Router } from 'express';
import { getSessionAssetById } from '../services/dbService';
import { generateSignedDownloadUrl } from '../services/blobService';

export const downloadsRouter = Router();

downloadsRouter.get('/:id', async (req, res, next) => {
  try {
    const userId = req.user!.id;
    const asset = await getSessionAssetById(req.params.id, userId);
    if (!asset) {
      res.status(404).json({ error: 'Asset not found' });
      return;
    }

    const expiresInSeconds = 36000;
    const signedUrl = generateSignedDownloadUrl(asset.blob_url, expiresInSeconds);
    const expiresAt = new Date(Date.now() + expiresInSeconds * 1000).toISOString();

    res.json({
      signed_url: signedUrl,
      filename: asset.filename,
      content_type: asset.content_type,
      asset_type: asset.asset_type,
      expires_at: expiresAt,
    });
  } catch (err) {
    next(err);
  }
});
