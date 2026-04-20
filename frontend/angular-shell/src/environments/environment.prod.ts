export const environment = {
  production: true,
  version: '1.0.0-beta',
  skipAuth: 'false',
  apiUrl: '/api',
  librechatUrl: '/chat',
  msalConfig: {
    clientId: '${AZURE_ENTRA_CLIENT_ID}',
    authority: 'https://login.microsoftonline.com/${AZURE_ENTRA_TENANT_ID}',
    redirectUri: '${APP_BASE_URL}',
  },
};
