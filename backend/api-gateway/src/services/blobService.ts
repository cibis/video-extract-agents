import crypto from 'crypto';
import path from 'path';
import {
  BlobServiceClient,
  BlobSASPermissions,
  SASProtocol,
} from '@azure/storage-blob';
import { config } from '../config';

let _blobServiceClient: BlobServiceClient | null = null;

export function getBlobServiceClient(): BlobServiceClient {
  if (!_blobServiceClient) {
    _blobServiceClient = BlobServiceClient.fromConnectionString(
      config.AZURE_STORAGE_CONNECTION_STRING
    );
  }
  return _blobServiceClient;
}

export async function generateSasUploadUrl(
  userId: string,
  videoId: string,
  filename?: string
): Promise<{ sasUrl: string; blobPath: string }> {
  const client = getBlobServiceClient();
  const containerClient = client.getContainerClient(config.AZURE_STORAGE_CONTAINER_NAME);
  const ext = filename ? path.extname(filename) : '';
  const blobPath = `${userId}/original/${videoId}${ext}`;
  const blobClient = containerClient.getBlobClient(blobPath);

  const expiresOn = new Date(Date.now() + 60 * 60 * 1000); // 1 hour

  let sasUrl = await blobClient.generateSasUrl({
    permissions: BlobSASPermissions.parse('cw'),
    expiresOn,
    protocol: SASProtocol.HttpsAndHttp,
  });

  // Azurite's internal hostname is unreachable from the browser; rewrite to localhost.
  if (config.OUTPUT_URL_MODE === 'local') {
    sasUrl = sasUrl.replace('http://azurite:10000', 'http://localhost:10000');
  }

  return { sasUrl, blobPath };
}

export function getAzuriteBlobUrl(blobPath: string): string {
  return `http://azurite:10000/devstoreaccount1/${config.AZURE_STORAGE_CONTAINER_NAME}/${blobPath}`;
}

/**
 * Returns the internal blob URL used by backend services to read a video.
 * In local dev: Azurite URL (http://azurite:10000/...).
 * In CI/prod: Azure Blob Storage URL derived from the storage connection string.
 */
export function getInternalBlobUrl(blobPath: string): string {
  if (config.OUTPUT_URL_MODE === 'local') {
    return getAzuriteBlobUrl(blobPath);
  }
  const client = getBlobServiceClient();
  return client
    .getContainerClient(config.AZURE_STORAGE_CONTAINER_NAME)
    .getBlobClient(blobPath)
    .url;
}

/**
 * Delete a single blob by container + blob path. Silently ignores 404.
 */
export async function deleteBlob(containerName: string, blobPath: string): Promise<void> {
  try {
    const client = getBlobServiceClient();
    await client.getContainerClient(containerName).getBlobClient(blobPath).deleteIfExists();
  } catch {
    // ignore errors (e.g. blob already gone)
  }
}

/**
 * Delete all blobs whose names start with the given prefix.
 * Returns the number of blobs deleted.
 */
export async function deleteBlobsByPrefix(containerName: string, prefix: string): Promise<number> {
  const client = getBlobServiceClient();
  const containerClient = client.getContainerClient(containerName);
  let deleted = 0;
  for await (const blob of containerClient.listBlobsFlat({ prefix })) {
    await containerClient.getBlobClient(blob.name).deleteIfExists();
    deleted++;
  }
  return deleted;
}

/**
 * Generate a signed download URL for any blob.
 * - local mode: returns blobUrl unchanged (direct Azurite access)
 * - frontdoor mode: applies HMAC-SHA256 Front Door signing with expiry
 */
export function generateSignedDownloadUrl(
  blobUrl: string,
  expirySeconds: number = 36000
): string {
  if (config.OUTPUT_URL_MODE === 'local') {
    // Convert internal Docker URL (http://azurite:10000/devstoreaccount1/...)
    // to a browser-accessible proxy URL (http://localhost:8000/v1/blob-proxy/...)
    try {
      const parsed = new URL(blobUrl);
      // pathname = /devstoreaccount1/videos/userId/... — strip the account segment
      const pathWithoutAccount = parsed.pathname.replace(/^\/[^/]+\//, '');
      return `${config.BLOB_PROXY_BASE_URL}/v1/blob-proxy/${pathWithoutAccount}`;
    } catch {
      return blobUrl;
    }
  }
  const expires = Math.floor(Date.now() / 1000) + expirySeconds;
  const urlPath = new URL(blobUrl).pathname;
  const baseUrl = `${config.FRONT_DOOR_ENDPOINT}${urlPath}`;
  const stringToSign = `${urlPath}\n${expires}`;
  const signature = crypto
    .createHmac('sha256', config.FRONT_DOOR_SECRET)
    .update(stringToSign)
    .digest('hex');
  return `${baseUrl}?expires=${expires}&sig=${signature}`;
}
