/**
 * Platform customisation entry point.
 * Export all platform-specific components and utilities from this file.
 */
export { PlatformConfig, PLATFORM_TITLE, PLATFORM_COLORS } from './PlatformConfig';
export { JobStatusBridge } from './JobStatusBridge';
export { AuthBridge, getEntraToken } from './AuthBridge';
export { GeneratedFilesPanel } from './GeneratedFilesPanel';
export type { OutputFile } from './GeneratedFilesPanel';
