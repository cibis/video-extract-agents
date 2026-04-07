import '../../../tests/setup';

jest.mock('axios');
jest.mock('../../../src/middleware/auth', () => ({
  authMiddleware: (req: any, _res: any, next: any) => {
    req.user = { id: 'test-user', email: 'test@example.com' };
    next();
  },
}));

import request from 'supertest';
import axios from 'axios';
import { createApp } from '../../../src/app';

const app = createApp();

describe('POST /v1/chat', () => {
  beforeEach(() => jest.clearAllMocks());

  it('proxies request to agent orchestrator and forwards x-job-id', async () => {
    const mockAxios = axios as jest.Mocked<typeof axios>;
    (mockAxios.post as jest.Mock).mockResolvedValue({
      status: 200,
      data: { message: 'Agent response' },
      headers: { 'x-job-id': 'job-abc' },
    });

    const res = await request(app)
      .post('/v1/chat')
      .send({ messages: [{ role: 'user', content: 'extract jumps' }] });

    expect(res.status).toBe(200);
    expect(res.headers['x-job-id']).toBe('job-abc');
    expect(res.body.message).toBe('Agent response');
  });
});
