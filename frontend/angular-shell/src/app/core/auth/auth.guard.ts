import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';
import { environment } from '../../../environments/environment';

export const authGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (environment.production && !auth.isAuthenticated()) {
    auth.login();
    return false;
  }
  return true;
};
