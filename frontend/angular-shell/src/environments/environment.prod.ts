export const environment = {
  production: true,
  version: '1.0.0-beta',
  skipAuth: 'false',
  apiUrl: '/api',
  librechatUrl: '/chat',
  // postMessage targetOrigin — must be a full origin, not a relative path.
  // LibreChat is proxied at the same origin as the Angular shell (/chat/),
  // so the correct targetOrigin is the angular-shell's own public URL.
  librechatOrigin: '${APP_BASE_URL}',
  msalConfig: {
    clientId: '${AZURE_ENTRA_CLIENT_ID}',
    authority: 'https://login.microsoftonline.com/${AZURE_ENTRA_TENANT_ID}',
    redirectUri: '${APP_BASE_URL}',
  },
};
