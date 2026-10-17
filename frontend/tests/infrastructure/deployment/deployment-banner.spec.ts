/**
 * Deployment banner coverage targets the real backend SSE stream and seeds
 * both the baseline snapshot and the follow-up update through the testing API.
 */
import { test, expect } from '../../support/fixtures';
import { makeUnique } from '../../support/helpers';
import { extractSseData, waitForSseEvent } from '../../support/helpers/test-events';

test.describe('Deployment banner streaming', () => {
test('surfaces backend-driven deployment updates', async ({
  page,
  frontendUrl,
  backendUrl,
  testEvents,
  appShell,
  deploymentSse,
}) => {
    await page.goto(frontendUrl);

    await testEvents.clearEvents();

    await deploymentSse.resetRequestId();
    const connectionStatus = await deploymentSse.ensureConnected();

    const openEvent = await waitForSseEvent(page, {
      streamId: 'connection',
      phase: 'open',
      event: 'connected',
      timeoutMs: 30000,
    });

    const connectionData = extractSseData<{ requestId?: string }>(openEvent);
    const requestId = connectionStatus.requestId ?? connectionData?.requestId ?? null;
    expect(requestId).toBeTruthy();
    if (!requestId) {
      return;
    }

    const baselineVersion = makeUnique('baseline');
    const versionLabel = makeUnique('playwright');

    const baselineResponse = await page.request.post(`${backendUrl}/api/testing/deployments/version`, {
      data: {
        request_id: requestId,
        version: baselineVersion,
      },
    });

    expect(baselineResponse.ok()).toBeTruthy();

    // Match on version value only — the backend does not include
    // correlation_id in version event payloads.
    await waitForSseEvent(page, {
      streamId: 'version',
      phase: 'message',
      event: 'version',
      matcher: event => {
        const payload = extractSseData<{ version?: string }>(event);
        return payload?.version === baselineVersion;
      },
      timeoutMs: 30000,
    });

    // The banner compares a version with the first one the app received;
    // wait until the baseline is that one before sending the next.
    await deploymentSse.waitForCurrentVersion(baselineVersion);

    const triggerResponse = await page.request.post(`${backendUrl}/api/testing/deployments/version`, {
      data: {
        request_id: requestId,
        version: versionLabel,
      },
    });

    expect(triggerResponse.ok()).toBeTruthy();

    const payloadEvent = await waitForSseEvent(page, {
      streamId: 'version',
      phase: 'message',
      event: 'version',
      matcher: event => {
        const payload = extractSseData<{ version?: string }>(event);
        return payload?.version === versionLabel;
      },
      timeoutMs: 30000,
    });

    const payload = extractSseData<{ version?: string }>(payloadEvent);
    expect(payload?.version).toBe(versionLabel);

    const bannerMessage = page.getByText('A new version of the app is available.', { exact: false });
    await expect(bannerMessage).toBeVisible();
    await expect(appShell.deploymentReloadButton).toBeVisible();

    const navigationPromise = page.waitForNavigation({ waitUntil: 'load' });
    await appShell.deploymentReloadButton.click();
    await navigationPromise;

    // After page reload, wait for the app to fully re-initialize before
    // interacting with the bridge. The old execution context is destroyed
    // by navigation, so we must wait for the new bridge registration.
    await page.waitForFunction(
      () => Boolean(window.__deploymentSseControls),
      undefined,
      { timeout: 30_000 },
    );

    await testEvents.clearEvents();
    await deploymentSse.ensureConnected();
    await waitForSseEvent(page, {
      streamId: 'connection',
      phase: 'open',
      event: 'connected',
      timeoutMs: 30000,
    });

    await expect(page.locator('[data-testid="deployment.banner"]')).toHaveCount(0);

    await deploymentSse.disconnect();
  });
});
