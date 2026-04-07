import '../../../tests/setup';

jest.mock('../../../src/services/dbService');
jest.mock('../../../src/middleware/auth', () => ({
  authMiddleware: (req: any, _res: any, next: any) => {
    req.user = { id: 'test-user', email: 'test@example.com' };
    next();
  },
}));

import request from 'supertest';
import { createApp } from '../../../src/app';
import * as dbService from '../../../src/services/dbService';

const app = createApp();

describe('GET /v1/outputs/:id', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns output URL for completed job (local mode)', async () => {
    const mockJob = {
      id: 'job-123',
      status: 'completed',
      output_url: 'http://azurite:10000/devstoreaccount1/videos/user/outputs/job-123/output.mp4',
    };
    (dbService.getJobById as jest.Mock).mockResolvedValue(mockJob);

    const res = await request(app).get('/v1/outputs/job-123');

    expect(res.status).toBe(200);
    expect(res.body.outputUrl).toBeDefined();
  });

  it('returns 404 when job not found', async () => {
    (dbService.getJobById as jest.Mock).mockResolvedValue(null);

    const res = await request(app).get('/v1/outputs/nonexistent');

    expect(res.status).toBe(404);
  });

  it('returns 404 when job not completed', async () => {
    (dbService.getJobById as jest.Mock).mockResolvedValue({
      id: 'job-123',
      status: 'processing',
      output_url: null,
    });

    const res = await request(app).get('/v1/outputs/job-123');

    expect(res.status).toBe(404);
  });
});
