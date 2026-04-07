import { Injectable, signal } from '@angular/core';
import { environment } from '../../../environments/environment';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private _isAuthenticated = signal(false);
  private _token = signal<string | null>(null);

  isAuthenticated = this._isAuthenticated.asReadonly();

  constructor() {
    // Restore token from session storage on init
    const stored = sessionStorage.getItem('auth_token');
    if (stored) {
      this._token.set(stored);
      this._isAuthenticated.set(true);
    }
  }

  login(): void {
    const { clientId, authority, redirectUri } = environment.msalConfig;
    const params = new URLSearchParams({
      client_id: clientId,
      response_type: 'code',
      redirect_uri: redirectUri,
      scope: 'openid profile email',
      response_mode: 'fragment',
    });
    window.location.href = `${authority}/oauth2/v2.0/authorize?${params}`;
  }

  logout(): void {
    sessionStorage.removeItem('auth_token');
    this._token.set(null);
    this._isAuthenticated.set(false);
    window.location.href = '/';
  }

  setToken(token: string): void {
    sessionStorage.setItem('auth_token', token);
    this._token.set(token);
    this._isAuthenticated.set(true);
  }

  getToken(): string | null {
    return this._token();
  }
}
