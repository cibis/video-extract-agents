import '../../../tests/setup';
import { Request, Response, NextFunction } from 'express';

jest.mock('jwks-rsa', () => ({
  __esModule: true,
  default: jest.fn().mockReturnValue({
    getSigningKey: jest.fn(),
  }),
}));
jest.mock('jsonwebtoken');
jest.mock('../../../src/config', () => ({
  config: {
    LOCAL_DEV_SKIP_AUTH: false,
    AZURE_ENTRA_JWKS_URI: 'https://example.com/jwks',
    AZURE_ENTRA_CLIENT_ID: 'test-client',
    AZURE_ENTRA_TENANT_ID: 'test-tenant',
  },
}));

import { authMiddleware } from '../../../src/middleware/auth';

const mockReq = (headers: Record<string, string> = {}): Partial<Request> => ({
  headers: headers as any,
});
const mockRes = (): Partial<Response> => {
  const res: any = {};
  res.status = jest.fn().mockReturnValue(res);
  res.json = jest.fn().mockReturnValue(res);
  return res;
};
const mockNext: NextFunction = jest.fn();

describe('authMiddleware', () => {
  beforeEach(() => jest.clearAllMocks());

  it('injects local-dev-user when LOCAL_DEV_SKIP_AUTH is true', async () => {
    const { config } = require('../../../src/config');
    config.LOCAL_DEV_SKIP_AUTH = true;

    const req = mockReq() as any;
    const res = mockRes() as any;
    const next = jest.fn();

    await authMiddleware(req, res, next);

    expect(req.user).toEqual({ id: 'local-dev-user', email: 'dev@local' });
    expect(next).toHaveBeenCalled();

    config.LOCAL_DEV_SKIP_AUTH = false;
  });

  it('returns 401 when Authorization header is missing', async () => {
    const { config } = require('../../../src/config');
    config.LOCAL_DEV_SKIP_AUTH = false;

    const req = mockReq() as any;
    const res = mockRes() as any;
    const next = jest.fn();

    await authMiddleware(req, res, next);

    expect(res.status).toHaveBeenCalledWith(401);
    expect(next).not.toHaveBeenCalled();
  });

  it('returns 401 when Authorization header does not start with Bearer', async () => {
    const { config } = require('../../../src/config');
    config.LOCAL_DEV_SKIP_AUTH = false;

    const req = mockReq({ authorization: 'Basic abc123' }) as any;
    const res = mockRes() as any;
    const next = jest.fn();

    await authMiddleware(req, res, next);

    expect(res.status).toHaveBeenCalledWith(401);
  });
});
