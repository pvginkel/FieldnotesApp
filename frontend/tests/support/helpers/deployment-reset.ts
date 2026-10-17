import { Page } from '@playwright/test';

const BRIDGE_WAIT_TIMEOUT = 30_000;

/**
 * Resets the deployment SSE request id via the Playwright bridge.
 *
 * The bridge is registered at module-load time by `src/lib/config/sse-request-id.ts`,
 * which is imported transitively from the SseContextProvider. Under preview mode
 * (production build) the SseContextProvider module can resolve after `page.goto`
 * returns, so we wait for the bridge to appear before invoking it — mirroring
 * the `waitForBridge` pattern in `deployment-sse.ts`.
 */
export async function resetDeploymentRequestId(page: Page): Promise<void> {
  await page.waitForFunction(
    () => typeof window.__resetDeploymentRequestId === 'function',
    undefined,
    { timeout: BRIDGE_WAIT_TIMEOUT }
  );
  await page.evaluate(() => {
    const reset = window.__resetDeploymentRequestId;
    if (typeof reset !== 'function') {
      throw new Error('Deployment request id reset bridge is not registered on window.');
    }
    reset();
  });
}
