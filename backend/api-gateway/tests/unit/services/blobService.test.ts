import '../../setup';

const mockBlobClient = {
  generateSasUrl: jest.fn().mockResolvedValue('https://blob.example.com/test?sas=token'),
};
const mockContainerClient = {
  getBlobClient: jest.fn().mockReturnValue(mockBlobClient),
};

jest.mock('@azure/storage-blob', () => ({
  BlobServiceClient: {
    fromConnectionString: jest.fn().mockReturnValue({
      getContainerClient: jest.fn().mockReturnValue(mockContainerClient),
    }),
  },
  BlobSASPermissions: { parse: jest.fn().mockReturnValue({}) },
  SASProtocol: { HttpsAndHttp: 'HttpsAndHttp' },
}));

import { generateSasUploadUrl } from '../../../src/services/blobService';

describe('blobService', () => {
  beforeEach(() => jest.clearAllMocks());

  it('generateSasUploadUrl returns sasUrl and blobPath', async () => {
    mockBlobClient.generateSasUrl.mockResolvedValue('https://blob.example.com/test?sas=token');
    mockContainerClient.getBlobClient.mockReturnValue(mockBlobClient);

    const result = await generateSasUploadUrl('user-1', 'video-1');

    expect(result.blobPath).toBe('user-1/original/video-1');
    expect(result.sasUrl).toContain('blob.example.com');
  });
});
