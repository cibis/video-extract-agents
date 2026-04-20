export const environment = {
  production: false,
  version: '1.0.0-beta',
  skipAuth: '${LOCAL_DEV_SKIP_AUTH}',
  apiUrl: 'http://localhost:8000',
  librechatUrl: 'http://localhost:3080',
  msalConfig: {
    clientId: '${AZURE_ENTRA_CLIENT_ID}',
    authority: 'https://login.microsoftonline.com/${AZURE_ENTRA_TENANT_ID}',
    redirectUri: '${APP_BASE_URL}',
  },
};
