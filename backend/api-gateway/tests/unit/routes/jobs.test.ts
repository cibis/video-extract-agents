import '../../../tests/setup';

jest.mock('../../../src/services/dbService');
jest.mock('../../../src/services/serviceBusService');
jest.mock('../../../src/middleware/auth', () => ({
  authMiddleware: (req: any, _res: any, next: any) => {
    req.user = { id: 'test-user', email: 'test@example.com' };
    next();
  },
}));

import request from 'supertest';
import { createApp } from '../../../src/app';
import * as dbService from '../../../src/services/dbService';
import * as sbService from '../../../src/services/serviceBusService';

const app = createApp();

describe('POST /v1/jobs', () => {
  beforeEach(() => jest.clearAllMocks());

  it('creates a job and returns 201', async () => {
    const mockJob = {
      id: 'job-123',
      user_id: 'test-user',
      video_id: 'video-456',
      prompt: 'extract jumps',
      status: 'queued',
      output_url: null,
      error: null,
      created_at: new Date(),
      updated_at: new Date(),
    };

    (dbService.createJob as jest.Mock).mockResolvedValue(mockJob);
    (sbService.publishJobQueued as jest.Mock).mockResolvedValue(undefined);

    const res = await request(app)
      .post('/v1/jobs')
      .send({ videoId: 'video-456', prompt: 'extract jumps' });

    expect(res.status).toBe(201);
    expect(res.body.jobId).toBe('job-123');
    expect(res.body.status).toBe('queued');
  });

  it('returns 400 when videoId is missing', async () => {
    const res = await request(app)
      .post('/v1/jobs')
      .send({ prompt: 'extract jumps' });

    expect(res.status).toBe(400);
  });

  it('returns 400 when prompt is missing', async () => {
    const res = await request(app)
      .post('/v1/jobs')
      .send({ videoId: 'video-456' });

    expect(res.status).toBe(400);
  });
});

describe('GET /v1/jobs/:id', () => {
  it('returns job when found', async () => {
    const mockJob = {
      id: 'job-123',
      status: 'completed',
      output_url: 'http://example.com/output.mp4',
    };
    (dbService.getJobById as jest.Mock).mockResolvedValue(mockJob);

    const res = await request(app).get('/v1/jobs/job-123');

    expect(res.status).toBe(200);
    expect(res.body.id).toBe('job-123');
  });

  it('returns 404 when job not found', async () => {
    (dbService.getJobById as jest.Mock).mockResolvedValue(null);

    const res = await request(app).get('/v1/jobs/nonexistent');

    expect(res.status).toBe(404);
  });
});
