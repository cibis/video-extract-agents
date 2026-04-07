import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import jwksClient from 'jwks-rsa';
import { config } from '../config';

export interface AuthenticatedUser {
  id: string;
  email: string;
}

declare global {
  namespace Express {
    interface Request {
      user?: AuthenticatedUser;
    }
  }
}

let jwksClientInstance: jwksClient.JwksClient | null = null;

function getJwksClient(): jwksClient.JwksClient {
  if (!jwksClientInstance) {
    jwksClientInstance = jwksClient({
      jwksUri: config.AZURE_ENTRA_JWKS_URI,
      cache: true,
      rateLimit: true,
    });
  }
  return jwksClientInstance;
}

function getSigningKey(kid: string): Promise<string> {
  return new Promise((resolve, reject) => {
    getJwksClient().getSigningKey(kid, (err, key) => {
      if (err) return reject(err);
      resolve(key!.getPublicKey());
    });
  });
}

export async function authMiddleware(
  req: Request,
  res: Response,
  next: NextFunction
): Promise<void> {
  if (config.LOCAL_DEV_SKIP_AUTH) {
    req.user = { id: '00000000-0000-0000-0000-000000000001', email: 'dev@local' };
    return next();
  }

  const authHeader = req.headers.authorization;
  if (!authHeader?.startsWith('Bearer ')) {
    res.status(401).json({ error: 'Missing or invalid Authorization header' });
    return;
  }

  const token = authHeader.slice(7);

  try {
    const decoded = jwt.decode(token, { complete: true });
    if (!decoded || typeof decoded === 'string') {
      res.status(401).json({ error: 'Invalid token format' });
      return;
    }

    const kid = decoded.header.kid;
    if (!kid) {
      res.status(401).json({ error: 'Token missing kid' });
      return;
    }

    const signingKey = await getSigningKey(kid);

    const payload = jwt.verify(token, signingKey, {
      audience: config.AZURE_ENTRA_CLIENT_ID,
      issuer: `https://login.microsoftonline.com/${config.AZURE_ENTRA_TENANT_ID}/v2.0`,
    }) as jwt.JwtPayload;

    req.user = {
      id: payload.sub ?? payload.oid ?? '',
      email: payload.email ?? payload.preferred_username ?? '',
    };

    next();
  } catch (err) {
    res.status(401).json({ error: 'Token validation failed' });
  }
}
