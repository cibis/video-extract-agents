import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import jwksClient from 'jwks-rsa';
import { config } from '../config';
import { upsertUser, findUserByEmail } from '../services/dbService';

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
    console.error('[auth] Missing or invalid Authorization header on', req.method, req.path);
    res.status(401).json({ error: 'Missing or invalid Authorization header' });
    return;
  }

  const token = authHeader.slice(7);

  // Service-to-service path: LibreChat calls the api-gateway with the static
  // AGENT_API_KEY.  It also sends X-Librechat-User-Email so we can resolve the
  // real user identity without a JWT.
  if (config.AGENT_API_KEY && token === config.AGENT_API_KEY) {
    const email = req.headers['x-librechat-user-email'] as string | undefined;
    if (!email) {
      res.status(401).json({ error: 'Service call missing X-Librechat-User-Email' });
      return;
    }
    try {
      const user = await findUserByEmail(email);
      if (!user) {
        res.status(401).json({ error: 'User not found' });
        return;
      }
      req.user = user;
      return next();
    } catch (err) {
      res.status(500).json({ error: 'User lookup failed' });
      return;
    }
  }

  try {
    const decoded = jwt.decode(token, { complete: true });
    if (!decoded || typeof decoded === 'string') {
      console.error('[auth] Invalid token format — first 20 chars:', token.slice(0, 20));
      res.status(401).json({ error: 'Invalid token format' });
      return;
    }

    const kid = decoded.header.kid;
    if (!kid) {
      console.error('[auth] Token missing kid, iss:', (decoded.payload as jwt.JwtPayload).iss);
      res.status(401).json({ error: 'Token missing kid' });
      return;
    }

    const signingKey = await getSigningKey(kid);

    const payload = jwt.verify(token, signingKey, {
      audience: config.AZURE_ENTRA_CLIENT_ID,
      issuer: `https://login.microsoftonline.com/${config.AZURE_ENTRA_TENANT_ID}/v2.0`,
    }) as jwt.JwtPayload;

    req.user = {
      id: payload.oid ?? payload.sub ?? '',
      email: payload.email ?? payload.preferred_username ?? '',
    };

    try {
      await upsertUser(req.user.id, req.user.email);
    } catch (err) {
      console.error('[auth] Failed to upsert user:', err);
      res.status(500).json({ error: 'Failed to provision user' });
      return;
    }

    next();
  } catch (err) {
    console.error('[auth] Token validation failed:', (err as Error).message);
    res.status(401).json({ error: 'Token validation failed' });
  }
}
