import { Router } from 'express';
import {
  listTestSessionBlobs,
  deleteTestSessions,
  listAllSessionBlobs,
  deleteAllSessions,
  clearToolCache,
} from '../services/dbService';
import { deleteBlob, deleteBlobsByPrefix } from '../services/blobService';
import { config } from '../config';

export const adminRouter = Router();

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Extract the blob path from an internal Azurite or Azure Blob URL.
 * Strips the scheme, host, and account prefix so we get just container/path.
 * Returns null if the URL cannot be parsed.
 */
function extractBlobPath(url: string): { containerName: string; blobPath: string } | null {
  try {
    const parsed = new URL(url);
    // pathname examples:
    //   /devstoreaccount1/videos/userId/original/videoId   (Azurite)
    //   /videos/userId/original/videoId                    (Azure)
    const parts = parsed.pathname.replace(/^\//, '').split('/');
    // Drop the account segment if it looks like an Azurite emulator account name
    if (parts[0] === 'devstoreaccount1') parts.shift();
    const containerName = parts.shift()!;
    const blobPath = parts.join('/');
    if (!containerName || !blobPath) return null;
    return { containerName, blobPath };
  } catch {
    return null;
  }
}

async function wipeBlobs(
  blobUrls: string[],
  blobPrefixes: string[],
): Promise<number> {
  let deleted = 0;

  for (const url of blobUrls) {
    const parsed = extractBlobPath(url);
    if (!parsed) continue;
    await deleteBlob(parsed.containerName, parsed.blobPath);
    deleted++;
  }

  for (const prefix of blobPrefixes) {
    deleted += await deleteBlobsByPrefix(config.AZURE_STORAGE_CONTAINER_NAME, prefix);
  }

  return deleted;
}

// ─── Routes ───────────────────────────────────────────────────────────────────

/** DELETE /v1/admin/wipe-test-data — delete all test sessions + their blobs */
adminRouter.delete('/wipe-test-data', async (req, res, next) => {
  try {
    const userId = req.user!.id;
    const { sessionIds, blobUrls, blobPrefixes } = await listTestSessionBlobs(userId);

    if (sessionIds.length === 0) {
      res.json({ sessionsDeleted: 0, blobsDeleted: 0 });
      return;
    }

    const blobsDeleted = await wipeBlobs(blobUrls, blobPrefixes);
    const sessionsDeleted = await deleteTestSessions(userId);
    const cacheRowsDeleted = await clearToolCache(userId);

    res.json({ sessionsDeleted, blobsDeleted, cacheRowsDeleted });
  } catch (err) {
    next(err);
  }
});

/** DELETE /v1/admin/wipe-all-data — delete ALL sessions + their blobs for this user */
adminRouter.delete('/wipe-all-data', async (req, res, next) => {
  try {
    const userId = req.user!.id;
    const { blobUrls, blobPrefixes } = await listAllSessionBlobs(userId);

    const blobsDeleted = await wipeBlobs(blobUrls, blobPrefixes);
    const sessionsDeleted = await deleteAllSessions(userId);
    const cacheRowsDeleted = await clearToolCache(userId);

    res.json({ sessionsDeleted, blobsDeleted, cacheRowsDeleted });
  } catch (err) {
    next(err);
  }
});
