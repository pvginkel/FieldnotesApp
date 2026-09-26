/**
 * Test mode detection logic for frontend testing infrastructure
 */

/**
 * Checks if the application is running in test mode
 * Test mode is enabled when VITE_TEST_MODE environment variable is set to 'true'
 */
export function isTestMode(): boolean {
  return import.meta.env.VITE_TEST_MODE === 'true';
}

/**
 * Constant for test mode status
 */
export const TEST_MODE = isTestMode();