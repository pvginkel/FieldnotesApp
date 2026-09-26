import type { ProxyOptions } from 'vite'

/**
 * App-owned proxy extensions — merged into the Vite dev server and preview proxy config.
 * Modify this file to add app-specific proxy entries (e.g. iframe targets, external APIs).
 * This file is never overwritten by template updates.
 */
export function loadAppProxies(): Record<string, ProxyOptions> {
  return {}
}
