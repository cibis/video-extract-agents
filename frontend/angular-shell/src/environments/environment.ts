export const environment = {
  production: false,
  version: '1.0.0-beta',
  skipAuth: '${LOCAL_DEV_SKIP_AUTH}',
  apiUrl: 'http://localhost:8000',
  librechatUrl: '/chat',
  librechatOrigin: '${APP_BASE_URL}',
  msalConfig: {
    clientId: '${AZURE_ENTRA_CLIENT_ID}',
    authority: 'https://login.microsoftonline.com/${AZURE_ENTRA_TENANT_ID}',
    redirectUri: '${APP_BASE_URL}',
  },
};
