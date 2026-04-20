// Application Insights must be initialised BEFORE all other imports
import * as appInsights from 'applicationinsights';
if (process.env.APPLICATIONINSIGHTS_CONNECTION_STRING) {
  appInsights
    .setup(process.env.APPLICATIONINSIGHTS_CONNECTION_STRING)
    .setAutoDependencyCorrelation(true)
    .setAutoCollectRequests(true)
    .setAutoCollectPerformance(true, false)
    .setAutoCollectExceptions(true)
    .setAutoCollectDependencies(true)
    .start();
}

import http from 'http';
import { createApp } from './app';
import { config } from './config';
import { sharedKeyAuthHeader } from './routes/blobProxy';
import { initializeSchema } from './services/dbService';

const AZURITE_API_VERSION = '2020-10-02';

function parseStorageConnectionString(cs: string): { accountName: string; accountKey: string } {
  const parts: Record<string, string> = {};
  for (const segment of cs.split(';')) {
    const eq = segment.indexOf('=');
    if (eq > 0) parts[segment.slice(0, eq)] = segment.slice(eq + 1);
  }
  return { accountName: parts['AccountName'], accountKey: parts['AccountKey'] };
}

async function ensureAzuriteContainer(): Promise<void> {
  const { accountName, accountKey } = parseStorageConnectionString(
    config.AZURE_STORAGE_CONNECTION_STRING
  );
  const url = `http://azurite:10000/${accountName}/${config.AZURE_STORAGE_CONTAINER_NAME}?restype=container`;

  const attempt = (): Promise<boolean> => {
    const date = new Date().toUTCString();
    const canonicalizedHeaders =
      `x-ms-date:${date}\n` +
      `x-ms-version:${AZURITE_API_VERSION}\n`;
    // Azurite emulator URL format: /accountName/container — account appears twice in canonical resource.
    // Content-Length: Azurite expects empty string for 0-length body in the string-to-sign.
    const canonicalizedResource = `/${accountName}/${accountName}/${config.AZURE_STORAGE_CONTAINER_NAME}\nrestype:container`;

    const authorization = sharedKeyAuthHeader({
      accountName, accountKey,
      method: 'PUT',
      contentLength: '',
      contentType: '',
      date,
      canonicalizedHeaders,
      canonicalizedResource,
    });

    return new Promise((resolve) => {
      const req = http.request(url, { method: 'PUT', headers: {
        authorization,
        'x-ms-date': date,
        'x-ms-version': AZURITE_API_VERSION,
        'content-length': '0',
      }}, (res) => {
        res.resume();
        if (res.statusCode === 201) {
          console.log(`Storage container '${config.AZURE_STORAGE_CONTAINER_NAME}' created.`);
          resolve(true);
        } else if (res.statusCode === 409) {
          console.log(`Storage container '${config.AZURE_STORAGE_CONTAINER_NAME}' already exists.`);
          resolve(true);
        } else {
          console.warn(`Storage container init returned ${res.statusCode}`);
          resolve(false);
        }
      });
      req.on('error', (err) => {
        console.warn('Storage container init failed (Azurite not ready?):', err.message);
        resolve(false);
      });
      req.end();
    });
  };

  const MAX_ATTEMPTS = 10;
  const DELAY_MS = 3_000;
  for (let i = 1; i <= MAX_ATTEMPTS; i++) {
    const ok = await attempt();
    if (ok) return;
    if (i < MAX_ATTEMPTS) {
      console.log(`Retrying storage container init in ${DELAY_MS / 1000}s (attempt ${i}/${MAX_ATTEMPTS})…`);
      await new Promise(r => setTimeout(r, DELAY_MS));
    }
  }
  console.error(`Storage container '${config.AZURE_STORAGE_CONTAINER_NAME}' could not be initialised after ${MAX_ATTEMPTS} attempts — uploads will fail.`);
}

async function main() {
  if (config.LOCAL_DEV_SKIP_AUTH) {
    console.warn('WARNING: LOCAL_DEV_SKIP_AUTH=true — JWT validation disabled');
  }

  // Initialise schema before the server starts accepting requests so no request
  // can arrive before tables exist. Includes retry logic for slow DB cold starts.
  await initializeSchema();

  if (config.OUTPUT_URL_MODE === 'local') {
    await ensureAzuriteContainer();
  }

  const app = createApp();
  const server = http.createServer(app);
  // Disable socket idle timeout and set generous keep-alive so long-running
  // agent calls (which can exceed several minutes) are never cut short.
  server.setTimeout(0);
  server.keepAliveTimeout = 3_600_000; // 1 hour
  server.headersTimeout  = 3_601_000; // must exceed keepAliveTimeout

  server.listen(config.PORT, () => {
    console.log(`API Gateway listening on port ${config.PORT} [${config.NODE_ENV}]`);
  });
}

main().catch(err => {
  console.error('Fatal startup error:', err);
  process.exit(1);
});
