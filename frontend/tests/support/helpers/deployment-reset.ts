import { Page } from '@playwright/test';

/**
 * Resets the deployment SSE request id via the Playwright bridge.
 */
export async function resetDeploymentRequestId(page: Page): Promise<void> {
  await page.evaluate(() => {
    const reset = window.__resetDeploymentRequestId;
    if (typeof reset !== 'function') {
      throw new Error('Deployment request id reset bridge is not registered on window.');
    }
    reset();
  });
}
