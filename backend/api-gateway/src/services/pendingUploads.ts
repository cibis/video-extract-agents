/**
 * In-memory registry for pending VIDEO_UPLOADED Service Bus events.
 *
 * In local dev mode (OUTPUT_URL_MODE=local), the api-gateway returns a
 * blob-proxy upload URL. The client uploads the file bytes to that URL
 * AFTER the POST /v1/videos response is returned. We must not publish
 * VIDEO_UPLOADED until the blob actually exists in Azurite; otherwise the
 * preprocessing worker races against the upload and dead-letters the message.
 *
 * Flow:
 *   POST /v1/videos  →  registers payload here, does NOT publish
 *   PUT  /v1/blob-proxy/<blobPath>  →  on success, dequeues and publishes
 */

export interface PendingVideoUpload {
  videoId: string;
  userId: string;
  blobUrl: string;
  sessionId?: string;
}

const _registry = new Map<string, PendingVideoUpload>();

/**
 * Register a pending VIDEO_UPLOADED event keyed by blobPath.
 * The blob-proxy PUT handler will call consumePendingUpload() after the
 * upload succeeds.
 */
export function registerPendingUpload(blobPath: string, payload: PendingVideoUpload): void {
  _registry.set(blobPath, payload);
}

/**
 * Remove and return the pending payload for blobPath, or undefined if none.
 * Called by the blob-proxy PUT handler after a successful Azurite write.
 */
export function consumePendingUpload(blobPath: string): PendingVideoUpload | undefined {
  const payload = _registry.get(blobPath);
  if (payload !== undefined) {
    _registry.delete(blobPath);
  }
  return payload;
}
