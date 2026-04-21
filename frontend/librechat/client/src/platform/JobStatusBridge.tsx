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

/**
 * Patch XMLHttpRequest to inject Authorization and X-Session-Id headers on
 * API Gateway requests.  LibreChat's OpenAI streaming client uses XHR, so
 * the fetch patch alone is not sufficient.
 *
 * Strategy:
 *   - Track the request URL in open().
 *   - Intercept setRequestHeader() to replace the LibreChat apiKey
 *     Authorization header with the real Entra token.
 *   - Inject X-Session-Id in send() (before it was ever set by LibreChat).
 */
function patchXhr(): () => void {
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  const originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (
    method: string,
    url: string | URL,
    ...rest: unknown[]
  ) {
    const urlStr = typeof url === 'string' ? url : url.toString();
    (this as any)._isApiGateway = urlStr.includes('/v1/');
    return (originalOpen as Function).call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.setRequestHeader = function (name: string, value: string) {
    if ((this as any)._isApiGateway && name.toLowerCase() === 'authorization') {
      const entraToken = getEntraToken();
      if (entraToken) {
        return originalSetRequestHeader.call(this, name, `Bearer ${entraToken}`);
      }
    }
    return originalSetRequestHeader.call(this, name, value);
  };

  XMLHttpRequest.prototype.send = function (...args: unknown[]) {
    if ((this as any)._isApiGateway) {
      const sessionId = getSessionId();
      if (sessionId) {
        originalSetRequestHeader.call(this, 'X-Session-Id', sessionId);
      }
    }
    return (originalSend as Function).call(this, ...args);
  };

  return () => {
    XMLHttpRequest.prototype.open = originalOpen;
    XMLHttpRequest.prototype.setRequestHeader = originalSetRequestHeader;
    XMLHttpRequest.prototype.send = originalSend;
  };
}

export const JobStatusBridge: React.FC = () => {
  useEffect(() => {
    const unpatchFetch = patchFetch();
    const unpatchXhr = patchXhr();
    return () => {
      unpatchFetch();
      unpatchXhr();
    };
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
