/**
 * Platform branding and configuration constants.
 * Override LibreChat defaults with Video Extract Platform branding.
 */

export const PLATFORM_TITLE = 'Video Extract Platform';

export const PLATFORM_COLORS = {
  primary: '#0078d4',
  primaryDark: '#005a9e',
  primaryLight: '#e3f2fd',
  accent: '#00b4d8',
  background: '#f5f5f5',
  text: '#1a1a1a',
};

export const PlatformConfig = {
  title: PLATFORM_TITLE,
  colors: PLATFORM_COLORS,
  logoUrl: '/logo.svg',
  supportUrl: null,
  feedbackUrl: null,
  // Single agent endpoint — do not show model selector
  singleEndpoint: true,
  endpointName: 'Video Extract Agent',
};
