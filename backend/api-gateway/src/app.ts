import express from 'express';
import cors from 'cors';
import { authMiddleware } from './middleware/auth';
import { errorHandler } from './middleware/errorHandler';
import { videosRouter } from './routes/videos';
import { jobsRouter } from './routes/jobs';
import { chatRouter } from './routes/chat';
import { outputsRouter } from './routes/outputs';
import { sessionsRouter } from './routes/sessions';
import { assetsRouter } from './routes/assets';
import { downloadsRouter } from './routes/downloads';
import { blobProxyRouter } from './routes/blobProxy';
import { adminRouter } from './routes/admin';
import { config } from './config';

export function createApp(): express.Application {
  const app = express();

  app.use(cors());

  // Blob upload proxy must be mounted before any body parsers so req remains
  // a raw readable stream that can be piped directly into Azure Blob Storage.
  if (config.OUTPUT_URL_MODE === 'local') {
    app.use('/v1/blob-proxy', blobProxyRouter);
  }

  app.use(express.json());

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', service: 'api-gateway' });
  });

  app.use('/v1/videos', authMiddleware, videosRouter);
  app.use('/v1/jobs', authMiddleware, jobsRouter);
  app.use('/v1/chat/completions', authMiddleware, chatRouter);
  app.use('/v1/outputs', authMiddleware, outputsRouter);
  app.use('/v1/sessions', authMiddleware, sessionsRouter);
  app.use('/v1/assets', authMiddleware, assetsRouter);
  app.use('/v1/downloads', authMiddleware, downloadsRouter);
  app.use('/v1/admin', authMiddleware, adminRouter);

  app.use(errorHandler);

  return app;
}
