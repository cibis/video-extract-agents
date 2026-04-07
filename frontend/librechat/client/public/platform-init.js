/**
 * platform-init.js — SSO bootstrap for LibreChat iframe.
 *
 * Injected into index.html BEFORE React hydrates so that localStorage is
 * populated with the LibreChat session token by the time the React app
 * checks authentication state.
 *
 * Angular shell provisions a LibreChat user and passes the resulting tokens
 * as query parameters when setting the iframe src:
 *   <iframe src="/...?lc_bootstrap_token=AAA&lc_refresh_token=BBB">
 *
 * This script reads those params, writes them to localStorage (LibreChat's
 * token storage keys), then removes them from the URL so they are not
 * visible in the browser address bar or retained in history.
 */
(function () {
  'use strict';

  // Inject platform favicon — replaces LibreChat's default icon.
  (function () {
    var link = document.createElement('link');
    link.rel = 'icon';
    link.type = 'image/svg+xml';
    link.href = '/favicon.svg';
    // Remove any existing favicon links before adding ours
    document.querySelectorAll('link[rel~="icon"]').forEach(function (el) {
      el.parentNode && el.parentNode.removeChild(el);
    });
    document.head.appendChild(link);
  })();

  var params = new URLSearchParams(window.location.search);
  var token = params.get('lc_bootstrap_token');
  var refreshToken = params.get('lc_refresh_token');

  if (token) {
    try {
      localStorage.setItem('token', token);
      if (refreshToken) {
        localStorage.setItem('refreshToken', refreshToken);
      }

      // Strip the bootstrap params from the URL without adding a history entry
      params.delete('lc_bootstrap_token');
      params.delete('lc_refresh_token');
      var cleanSearch = params.toString();
      var cleanUrl =
        window.location.pathname +
        (cleanSearch ? '?' + cleanSearch : '') +
        window.location.hash;
      window.history.replaceState(null, '', cleanUrl);
    } catch (e) {
      // localStorage may be blocked in some iframe sandboxing configurations
      console.warn('[platform-init] Could not write SSO tokens to localStorage:', e);
    }
  }
})();
