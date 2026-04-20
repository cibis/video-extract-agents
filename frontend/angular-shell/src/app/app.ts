import { Component } from '@angular/core';
import { RouterOutlet, RouterLink } from '@angular/router';
import { AuthService } from './core/auth/auth.service';
import { environment } from '../environments/environment';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink],
  template: `
    <header class="app-header">
      <div class="container app-header__inner">
        <a routerLink="/" class="app-header__logo"><img src="favicon.svg" width="28" height="28" alt="" class="app-header__logo-icon"> AI Video Extract</a>
        <nav class="app-header__nav">
          <a routerLink="/">Home</a>
          <a routerLink="/dashboard">Session History</a>
          @if (auth.isAuthenticated()) {
            <button (click)="auth.logout()">Sign Out</button>
          } @else {
            <button (click)="auth.login()">Sign In</button>
          }
          <span class="app-header__version">v{{ version }}</span>
        </nav>
      </div>
    </header>
    <main class="app-main">
      <router-outlet />
    </main>
  `,
  styles: [`
    .app-header {
      background: #0078d4;
      color: white;
      padding: 0.75rem 0;
    }
    .app-header__inner {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .app-header__logo {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      font-size: 1.1rem;
      font-weight: 700;
      color: white;
      text-decoration: none;
    }
    .app-header__logo-icon {
      border-radius: 6px;
      flex-shrink: 0;
    }
    .app-header__nav {
      display: flex;
      gap: 1.5rem;
      align-items: center;
      a { color: rgba(255,255,255,0.9); }
      button {
        background: rgba(255,255,255,0.15);
        border: 1px solid rgba(255,255,255,0.4);
        color: white;
        padding: 0.4rem 1rem;
        border-radius: 4px;
        font-size: 0.875rem;
      }
    }
    .app-header__version {
      font-size: 0.7rem;
      color: rgba(255,255,255,0.55);
      letter-spacing: 0.02em;
    }
    .app-main {
      padding: 2rem 1.5rem 2rem;
      width: 100%;
    }
  `],
})
export class AppComponent {
  readonly version = environment.version;
  constructor(public auth: AuthService) {}
}
