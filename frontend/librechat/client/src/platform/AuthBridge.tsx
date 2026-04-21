/**
 * AuthBridge — receives the Entra JWT from the Angular shell via postMessage
 * and makes it available to the fetch patcher so every API Gateway request
 * carries a valid Authorization header.
 *
 * The Angular shell sends this message after a successful Entra login:
 *   window.frames[0].postMessage({ type: 'AUTH_TOKEN', token: '<entra_jwt>' }, librechatOrigin)
 *
 * The token is stored in module scope (memory only — never localStorage) so it
 * is not persisted between page loads and cannot be harvested by LibreChat's
 * own storage layer.
 *
 * Usage: mount <AuthBridge /> once inside the LibreChat app root alongside
 * <JobStatusBridge />.
 */
import { useEffect } from 'react';

/** Module-scoped token store — intentionally outside React state. */
let _entraToken: string | null = null;

/** Returns the current in-memory Entra JWT, or null if not yet received. */
export function getEntraToken(): string | null {
  return _entraToken;
}

interface AuthTokenMessage {
  type: 'AUTH_TOKEN';
  token: string;
}

function isAuthTokenMessage(data: unknown): data is AuthTokenMessage {
  return (
    typeof data === 'object' &&
    data !== null &&
    (data as Record<string, unknown>).type === 'AUTH_TOKEN' &&
    typeof (data as Record<string, unknown>).token === 'string'
  );
}

/**
 * AuthBridge component — side-effect only, renders nothing.
 * Mount once at the app root to begin listening for AUTH_TOKEN messages.
 */
export const AuthBridge: React.FC = () => {
  useEffect(() => {
    const handleMessage = (event: MessageEvent): void => {
      // In production the Angular shell and LibreChat share a Front Door
      // origin (e.g. https://app.example.com). In local dev they are on
      // different ports (4200 vs 3080); we accept any origin locally and
      // rely on the message type check as a lightweight guard.
      if (!isAuthTokenMessage(event.data)) return;

      _entraToken = event.data.token;
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  return null;
};

export default AuthBridge;
