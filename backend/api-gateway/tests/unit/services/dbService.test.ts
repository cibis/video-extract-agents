import '../../setup';

jest.mock('pg', () => {
  const mockPool = {
    query: jest.fn(),
  };
  return { Pool: jest.fn(() => mockPool) };
});

import { Pool } from 'pg';
import { createJob, getJobById } from '../../../src/services/dbService';

const mockPool = new (Pool as jest.Mock)();

describe('dbService', () => {
  beforeEach(() => jest.clearAllMocks());

  it('createJob inserts and returns job', async () => {
    const mockJob = { id: 'job-1', status: 'queued' };
    (mockPool.query as jest.Mock).mockResolvedValue({ rows: [mockJob] });

    const job = await createJob({
      id: 'job-1',
      userId: 'user-1',
      videoId: 'video-1',
      prompt: 'test prompt',
    });

    expect(mockPool.query).toHaveBeenCalledTimes(1);
    expect(job.id).toBe('job-1');
  });

  it('getJobById returns null when not found', async () => {
    (mockPool.query as jest.Mock).mockResolvedValue({ rows: [] });

    const job = await getJobById('nonexistent');

    expect(job).toBeNull();
  });

  it('getJobById returns job when found', async () => {
    const mockJob = { id: 'job-1', status: 'completed' };
    (mockPool.query as jest.Mock).mockResolvedValue({ rows: [mockJob] });

    const job = await getJobById('job-1');

    expect(job).toEqual(mockJob);
  });
});
