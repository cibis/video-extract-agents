import { inject } from '@angular/core';
import { CanActivateFn } from '@angular/router';
import { AuthService } from './auth.service';

/** Home route guard — handles the OAuth callback, then allows access. */
export const authGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);

  if (window.location.hash.includes('code=')) {
    await auth.handleCallback();
  }

  return true;
};

/**
 * Protected route guard — redirects unauthenticated users to the Entra
 * sign-in flow.  Skip-auth mode is always allowed through.
 */
export const requireAuthGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);

  if (auth.skipAuthMode() || auth.isAuthenticated()) {
    return true;
  }

  await auth.login();
  return false;
};
