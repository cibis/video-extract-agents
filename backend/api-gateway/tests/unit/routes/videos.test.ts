import '../../../tests/setup';

jest.mock('../../../src/services/blobService');
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
import * as blobService from '../../../src/services/blobService';
import * as dbService from '../../../src/services/dbService';
import * as sbService from '../../../src/services/serviceBusService';

const app = createApp();

describe('POST /v1/videos', () => {
  beforeEach(() => jest.clearAllMocks());

  it('returns SAS URL and videoId on success', async () => {
    (blobService.generateSasUploadUrl as jest.Mock).mockResolvedValue({
      sasUrl: 'https://blob.example.com/videos/test-user/original/vid-1?sas=token',
      blobPath: 'test-user/original/vid-1',
    });
    (dbService.createVideoRecord as jest.Mock).mockResolvedValue(undefined);
    (sbService.publishVideoUploaded as jest.Mock).mockResolvedValue(undefined);

    const res = await request(app).post('/v1/videos');

    expect(res.status).toBe(201);
    expect(res.body.videoId).toBeDefined();
    expect(res.body.sasUrl).toBeDefined();
    expect(res.body.blobPath).toBeDefined();
  });
});
