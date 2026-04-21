import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { AuthService } from '../auth/auth.service';

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const token = auth.getToken();

  // Only attach auth to our own API — relative URLs (nginx proxy) or localhost.
  // External absolute URLs (e.g. blob storage SAS uploads) carry auth in the
  // query string; adding a Bearer header causes Azure to reject with dual-auth error.
  const isOurApi = !req.url.startsWith('http') || req.url.startsWith('http://localhost');

  if (token && isOurApi) {
    const cloned = req.clone({
      setHeaders: { Authorization: `Bearer ${token}` },
    });
    return next(cloned);
  }
  return next(req);
};
