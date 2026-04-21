import { Injectable, computed, signal } from '@angular/core';
import { environment } from '../../../environments/environment';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private _isAuthenticated = signal(false);
  private _token = signal<string | null>(null);

  isAuthenticated = this._isAuthenticated.asReadonly();

  /** True when LOCAL_DEV_SKIP_AUTH=true is set on the backend (and propagated to this bundle). */
  readonly skipAuthMode = computed(() => {
    if (environment.skipAuth === 'true') return true;
    const { clientId } = environment.msalConfig;
    return !clientId || clientId.startsWith('${');
  });

  /** True when real credentials are configured but the user has not signed in. */
  readonly signInRequired = computed(() => !this.skipAuthMode() && !this._isAuthenticated());

  constructor() {
    // Restore token from session storage on init
    const stored = sessionStorage.getItem('auth_token');
    if (stored) {
      this._token.set(stored);
      this._isAuthenticated.set(true);
    }
  }

  async login(): Promise<void> {
    const { clientId, authority, redirectUri } = environment.msalConfig;
    const verifier = this.generateCodeVerifier();
    sessionStorage.setItem('pkce_code_verifier', verifier);
    const challenge = await this.generateCodeChallenge(verifier);
    const params = new URLSearchParams({
      client_id: clientId,
      response_type: 'code',
      redirect_uri: redirectUri,
      scope: 'openid profile email',
      response_mode: 'fragment',
      code_challenge: challenge,
      code_challenge_method: 'S256',
    });
    window.location.href = `${authority}/oauth2/v2.0/authorize?${params}`;
  }

  async handleCallback(): Promise<void> {
    const hash = window.location.hash.substring(1);
    const params = new URLSearchParams(hash);
    const code = params.get('code');
    if (!code) return;

    const verifier = sessionStorage.getItem('pkce_code_verifier');
    if (!verifier) return;

    const { clientId, authority, redirectUri } = environment.msalConfig;
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: clientId,
      code,
      redirect_uri: redirectUri,
      code_verifier: verifier,
      scope: 'openid profile email',
    });

    const response = await fetch(`${authority}/oauth2/v2.0/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    const tokens = await response.json();
    if (tokens.id_token) {
      this.setToken(tokens.id_token);
      sessionStorage.removeItem('pkce_code_verifier');
      window.history.replaceState(null, '', window.location.pathname);
    }
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

  private generateCodeVerifier(): string {
    const array = new Uint8Array(96);
    crypto.getRandomValues(array);
    return btoa(String.fromCharCode(...array))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  private async generateCodeChallenge(verifier: string): Promise<string> {
    const data = new TextEncoder().encode(verifier);
    const digest = await crypto.subtle.digest('SHA-256', data);
    return btoa(String.fromCharCode(...new Uint8Array(digest)))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }
}
