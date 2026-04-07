/**
 * JobStatusBridge — injects auth/session headers on API Gateway requests via
 * patched fetch.  Job progress polling and UI panels have moved to the Angular
 * shell host page.
 *
 * Usage: Mount this component inside the LibreChat app root.
 */
import React, { useEffect } from 'react';
import { getEntraToken } from './AuthBridge';

/** Module-scoped session ID — set via SESSION_CONTEXT postMessage from Angular shell. */
let _sessionId: string | null = null;

/** Returns the current session ID sent by the Angular shell, or null if not yet set. */
export function getSessionId(): string | null {
  return _sessionId;
}

interface SessionContextMessage {
  type: 'SESSION_CONTEXT';
  sessionId: string;
  videoIds: string[];
  apiUrl?: string;
}

function isSessionContextMessage(data: unknown): data is SessionContextMessage {
  return (
    typeof data === 'object' &&
    data !== null &&
    (data as Record<string, unknown>).type === 'SESSION_CONTEXT' &&
    typeof (data as Record<string, unknown>).sessionId === 'string'
  );
}

/**
 * Returns true when the request URL targets the API Gateway.
 */
function isApiGatewayRequest(input: RequestInfo | URL): boolean {
  const url =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : (input as Request).url;
  return url.includes('/v1/');
}

/**
 * Wrap the global fetch to inject Authorization and X-Session-Id headers
 * on all API Gateway requests.
 */
function patchFetch(): () => void {
  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    let patchedInit = init;

    const token = getEntraToken();
    const sessionId = getSessionId();
    if (isApiGatewayRequest(input) && (token || sessionId)) {
      patchedInit = {
        ...init,
        headers: {
          ...(init?.headers ?? {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(sessionId ? { 'X-Session-Id': sessionId } : {}),
        },
      };
    }

    return originalFetch(input, patchedInit);
  };

  return () => {
    window.fetch = originalFetch;
  };
}

export const JobStatusBridge: React.FC = () => {
  useEffect(() => {
    return patchFetch();
  }, []);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (isSessionContextMessage(event.data)) {
        _sessionId = event.data.sessionId;
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  return null;
};

export default JobStatusBridge;
