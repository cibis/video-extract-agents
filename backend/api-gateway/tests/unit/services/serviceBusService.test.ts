import '../../setup';

const mockSender = {
  sendMessages: jest.fn().mockResolvedValue(undefined),
  close: jest.fn().mockResolvedValue(undefined),
};

jest.mock('@azure/service-bus', () => ({
  ServiceBusClient: jest.fn().mockImplementation(() => ({
    createSender: jest.fn().mockReturnValue(mockSender),
  })),
}));

import { publishJobQueued, publishVideoUploaded } from '../../../src/services/serviceBusService';

describe('serviceBusService', () => {
  beforeEach(() => jest.clearAllMocks());

  it('publishJobQueued sends message to job-queued queue', async () => {
    await publishJobQueued({
      jobId: 'job-1',
      userId: 'user-1',
      videoId: 'video-1',
      prompt: 'test',
    });

    expect(mockSender.sendMessages).toHaveBeenCalledWith({
      body: { jobId: 'job-1', userId: 'user-1', videoId: 'video-1', prompt: 'test' },
      contentType: 'application/json',
    });
    expect(mockSender.close).toHaveBeenCalled();
  });

  it('publishVideoUploaded sends message to video-uploaded queue', async () => {
    await publishVideoUploaded({
      videoId: 'video-1',
      userId: 'user-1',
      blobUrl: 'http://example.com/blob',
    });

    expect(mockSender.sendMessages).toHaveBeenCalled();
    expect(mockSender.close).toHaveBeenCalled();
  });
});
