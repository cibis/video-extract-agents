export const environment = {
  production: true,
  apiUrl: '/api',
  librechatUrl: '/chat',
  msalConfig: {
    clientId: '${AZURE_ENTRA_CLIENT_ID}',
    authority: 'https://login.microsoftonline.com/${AZURE_ENTRA_TENANT_ID}',
    redirectUri: '${APP_BASE_URL}',
  },
};
