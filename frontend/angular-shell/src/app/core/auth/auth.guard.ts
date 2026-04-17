import { inject } from '@angular/core';
import { CanActivateFn } from '@angular/router';
import { AuthService } from './auth.service';
import { environment } from '../../../environments/environment';

export const authGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);

  if (window.location.hash.includes('code=')) {
    await auth.handleCallback();
    return true;
  }

  if (environment.production && !auth.isAuthenticated()) {
    auth.login();
    return false;
  }
  return true;
};
