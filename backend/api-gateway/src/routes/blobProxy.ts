import http from 'http';
import crypto from 'crypto';
import { Router } from 'express';
import { config } from '../config';
import { consumePendingUpload } from '../services/pendingUploads';
import { publishVideoUploaded } from '../services/serviceBusService';

export const blobProxyRouter = Router();

// Pinned to a version Azurite reliably supports for SharedKey auth
const AZURITE_API_VERSION = '2020-10-02';

/** Extract AccountName and AccountKey from a Storage connection string. */
function parseStorageConnectionString(cs: string): { accountName: string; accountKey: string } {
  const parts: Record<string, string> = {};
  for (const segment of cs.split(';')) {
    const eq = segment.indexOf('=');
    if (eq > 0) parts[segment.slice(0, eq)] = segment.slice(eq + 1);
  }
  return { accountName: parts['AccountName'], accountKey: parts['AccountKey'] };
}

/**
 * Compute an Azure Storage SharedKey Authorization header.
 * String-to-sign format has been stable since API version 2009-09-19.
 */
export function sharedKeyAuthHeader(opts: {
  accountName: string;
  accountKey: string;
  method: string;
  contentLength: string;
  contentType: string;
  date: string;
  range?: string;
  canonicalizedHeaders: string; // sorted x-ms-* headers, each "key:value\n"
  canonicalizedResource: string; // "/{account}/{container}/{blob}"
}): string {
  const stringToSign =
    [
      opts.method,
      '',              // Content-Encoding
      '',              // Content-Language
      opts.contentLength,
      '',              // Content-MD5
      opts.contentType,
      '',              // Date (suppressed — using x-ms-date instead)
      '',              // If-Modified-Since
      '',              // If-Match
      '',              // If-None-Match
      '',              // If-Unmodified-Since
      opts.range ?? '',// Range
    ].join('\n') + '\n' +
    opts.canonicalizedHeaders +   // each line already ends with \n
    opts.canonicalizedResource;   // NO separator — follows directly after headers

  const key = Buffer.from(opts.accountKey, 'base64');
  const sig = crypto.createHmac('sha256', key).update(stringToSign, 'utf8').digest('base64');
  return `SharedKey ${opts.accountName}:${sig}`;
}

/**
 * Local-dev-only proxy: GET /v1/blob-proxy/<container>/<blobPath>
 *
 * Streams a blob from Azurite to the browser using SharedKey auth.
 * Used to serve generated output video download links.
 */
blobProxyRouter.get('/*', (req, res, next) => {
  try {
    // blobPath already includes the container, e.g. "videos/userId/highlights/foo.mp4"
    const blobPath = (req.params as Record<string, string>)[0];

    const { accountName, accountKey } = parseStorageConnectionString(
      config.AZURE_STORAGE_CONNECTION_STRING
    );

    const date = new Date().toUTCString();
    const rangeHeader = req.headers['range'] as string | undefined;

    const canonicalizedHeaders =
      `x-ms-date:${date}\n` +
      `x-ms-version:${AZURITE_API_VERSION}\n`;

    // Azurite emulator doubles accountName in the canonical resource
    const canonicalizedResource = `/${accountName}/${accountName}/${blobPath}`;

    const authorization = sharedKeyAuthHeader({
      accountName,
      accountKey,
      method: 'GET',
      contentLength: '',
      contentType: '',
      date,
      range: rangeHeader,
      canonicalizedHeaders,
      canonicalizedResource,
    });

    const proxyHeaders: Record<string, string> = {
      authorization,
      'x-ms-date': date,
      'x-ms-version': AZURITE_API_VERSION,
    };
    if (rangeHeader) proxyHeaders['range'] = rangeHeader;

    const azuriteUrl = `http://azurite:10000/${accountName}/${blobPath}`;

    const proxyReq = http.request(
      azuriteUrl,
      { method: 'GET', headers: proxyHeaders },
      (proxyRes) => {
        res.writeHead(proxyRes.statusCode ?? 200, proxyRes.headers);
        proxyRes.pipe(res);
      }
    );

    proxyReq.on('error', next);
    proxyReq.end();
  } catch (err) {
    next(err);
  }
});

/**
 * Local-dev-only proxy: PUT /v1/blob-proxy/<blobPath>
 *
 * The browser sends the file to this endpoint; the API gateway pipes it to
 * Azurite using a manually-computed SharedKey Authorization header pinned to
 * API version 2020-10-02 — bypassing the @azure/storage-blob SDK whose
 * built-in API version (2026-xx) causes HMAC mismatches against Azurite.
 *
 * Only mounted when OUTPUT_URL_MODE === 'local'.
 */
blobProxyRouter.put('/*', (req, res, next) => {
  try {
    const blobPath = (req.params as Record<string, string>)[0];
    const contentType = req.headers['content-type'] ?? 'application/octet-stream';
    const contentLength = req.headers['content-length'] ?? '';

    const { accountName, accountKey } = parseStorageConnectionString(
      config.AZURE_STORAGE_CONNECTION_STRING
    );

    const date = new Date().toUTCString();

    // CanonicalizedHeaders: sorted x-ms-* headers, lower-cased, one per line
    const canonicalizedHeaders =
      `x-ms-blob-type:BlockBlob\n` +
      `x-ms-date:${date}\n` +
      `x-ms-version:${AZURITE_API_VERSION}\n`;

    // Azurite uses emulator URL format: /accountName/container/blob
    // SharedKey canonicalized resource = /{accountName} + URL-path, so account name appears twice.
    const canonicalizedResource = `/${accountName}/${accountName}/${config.AZURE_STORAGE_CONTAINER_NAME}/${blobPath}`;

    const authorization = sharedKeyAuthHeader({
      accountName,
      accountKey,
      method: 'PUT',
      contentLength,
      contentType,
      date,
      canonicalizedHeaders,
      canonicalizedResource,
    });

    const azuriteUrl =
      `http://azurite:10000/${accountName}` +
      `/${config.AZURE_STORAGE_CONTAINER_NAME}/${blobPath}`;

    const reqHeaders: Record<string, string> = {
      authorization,
      'content-type': contentType,
      'x-ms-blob-type': 'BlockBlob',
      'x-ms-date': date,
      'x-ms-version': AZURITE_API_VERSION,
    };
    if (contentLength) reqHeaders['content-length'] = contentLength;

    const proxyReq = http.request(
      azuriteUrl,
      { method: 'PUT', headers: reqHeaders },
      (proxyRes) => {
        if (proxyRes.statusCode && proxyRes.statusCode >= 400) {
          let body = '';
          proxyRes.on('data', (chunk: Buffer) => { body += chunk.toString(); });
          proxyRes.on('end', () =>
            next(new Error(`Azurite ${proxyRes.statusCode}: ${body}`))
          );
        } else {
          proxyRes.resume();
          res.status(201).end();
          // Blob is now committed to Azurite. If a VIDEO_UPLOADED event was
          // deferred for this blobPath (local dev mode), publish it now so
          // the preprocessing worker reads a blob that actually exists.
          const pending = consumePendingUpload(blobPath);
          if (pending) {
            publishVideoUploaded(pending).catch((err: unknown) => {
              console.error('[blobProxy] Failed to publish VIDEO_UPLOADED after PUT:', err);
            });
          }
        }
      }
    );

    proxyReq.on('error', next);
    req.pipe(proxyReq);
  } catch (err) {
    next(err);
  }
});
