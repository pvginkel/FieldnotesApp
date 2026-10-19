/**
 * Recovery from a refused SSE stream.
 *
 * The backend refuses a stream with 401 or 403 in-stream: it opens the
 * stream, sends a named `rejected` event carrying `{status, message}` and
 * ends it. The app must not retry that
 * stream in a loop. It checks the session through /api/auth/self, and then
 * opens a new stream when the session is valid, leaves the redirect to login
 * to the API client on 401, and shows a connection-lost toast otherwise.
 *
 * `POST /api/testing/sse/force-callback-error` makes the next connect refuse
 * once, `auth.forceError()` decides the session check, and the test-only
 * `window.__sseForceReconnect()` bridge opens that next stream while the
 * connection is still healthy.
 *
 * Every case runs on both paths: the direct EventSource in every run, and the
 * SharedWorker in preview mode only, since the dev server never uses it.
 */
import type { Page } from '@playwright/test';
import { test, expect } from '../../support/fixtures';
import { makeUnique } from '../../support/helpers';
import type { DeploymentSseHelper } from '../../support/helpers/deployment-sse';
import {
  extractSseData,
  getTestEventBuffer,
  waitForSseEvent,
} from '../../support/helpers/test-events';

// The SharedWorker cases need a production build: they skip unless
// PLAYWRIGHT_PREVIEW_MODE=true, and their @preview tag has the root suite
// runner's preview pass run them.
const PREVIEW_ONLY_REASON =
  'Needs PLAYWRIGHT_PREVIEW_MODE=true: the Vite dev server never uses the SharedWorker';

const PATHS = [
  { name: 'direct EventSource', query: '', sharedWorker: false },
  { name: 'SharedWorker', query: '?__sharedWorker', sharedWorker: true },
] as const;

type Path = (typeof PATHS)[number];

/** The URLs of the browser's shared workers (Chromium, through CDP). */
async function sharedWorkerUrls(page: Page): Promise<string[]> {
  const session = await page.context().newCDPSession(page);
  try {
    const { targetInfos } = await session.send('Target.getTargets');
    return targetInfos.filter(target => target.type === 'shared_worker').map(target => target.url);
  } finally {
    await session.detach();
  }
}

/**
 * Load the app on the given path and wait for its stream. Returns the stream's
 * request ID. Also checks that the stream runs where the path says, so that a
 * SharedWorker case can't pass on the direct connection.
 */
async function openApp(
  page: Page,
  frontendUrl: string,
  path: Path,
  deploymentSse: DeploymentSseHelper
): Promise<string> {
  await page.goto(`${frontendUrl}${path.query}`);
  const { requestId } = await deploymentSse.ensureConnected();
  if (!requestId) {
    throw new Error('The connected stream has no request ID');
  }

  const workers = (await sharedWorkerUrls(page)).filter(url => url.includes('sse-worker'));
  expect(workers).toHaveLength(path.sharedWorker ? 1 : 0);

  return requestId;
}

/** Make the next connect refuse with the given status. */
async function armConnectRefusal(page: Page, backendUrl: string, status: 401 | 403): Promise<void> {
  const response = await page.request.post(
    `${backendUrl}/api/testing/sse/force-callback-error?status=${status}`
  );
  expect(response.status()).toBe(204);
}

/** Open a new stream now, healthy connection or not. */
async function forceReconnect(page: Page): Promise<void> {
  await page.waitForFunction(() => typeof window.__sseForceReconnect === 'function', undefined, {
    timeout: 5000,
  });
  // Events from here on are the new stream's.
  getTestEventBuffer(page).clear();
  await page.evaluate(() => {
    const force = window.__sseForceReconnect;
    if (typeof force !== 'function') {
      throw new Error('The __sseForceReconnect bridge is not registered on window.');
    }
    force();
  });
}

async function expectRejected(page: Page, status: number): Promise<void> {
  const rejected = await waitForSseEvent(page, {
    streamId: 'connection',
    phase: 'message',
    event: 'rejected',
    timeoutMs: 15000,
  });
  expect(extractSseData<{ status?: number }>(rejected)?.status).toBe(status);
}

for (const path of PATHS) {
  const tag = path.sharedWorker ? ['@preview'] : [];
  test.describe(`SSE refusal recovery (${path.name})`, { tag }, () => {
    test('opens a new stream when the session is valid', async ({
      page,
      frontendUrl,
      backendUrl,
      deploymentSse,
    }) => {
      test.skip(path.sharedWorker && process.env.PLAYWRIGHT_PREVIEW_MODE !== 'true', PREVIEW_ONLY_REASON);

      const initialRequestId = await openApp(page, frontendUrl, path, deploymentSse);

      await armConnectRefusal(page, backendUrl, 401);
      await forceReconnect(page);
      await expectRejected(page, 401);

      // The session check passes (the Playwright backend runs with OIDC off),
      // so a new stream opens, with a new request ID.
      const connected = await waitForSseEvent(page, {
        streamId: 'connection',
        phase: 'open',
        event: 'connected',
        timeoutMs: 15000,
      });
      const requestId = extractSseData<{ requestId?: string }>(connected)?.requestId;
      expect(requestId).toBeTruthy();
      expect(requestId).not.toBe(initialRequestId);
      await expect
        .poll(() => deploymentSse.getStatus())
        .toEqual({ isConnected: true, requestId });

      // And it delivers events.
      const version = makeUnique('sse-recovery');
      const response = await page.request.post(`${backendUrl}/api/testing/deployments/version`, {
        data: { request_id: requestId, version },
      });
      expect(response.ok()).toBeTruthy();

      await waitForSseEvent(page, {
        streamId: 'version',
        phase: 'message',
        event: 'version',
        matcher: event => extractSseData<{ version?: string }>(event)?.version === version,
        timeoutMs: 15000,
      });
    });

    test('sends the user to login when the session is gone', async ({
      page,
      frontendUrl,
      backendUrl,
      auth,
      deploymentSse,
    }) => {
      test.skip(path.sharedWorker && process.env.PLAYWRIGHT_PREVIEW_MODE !== 'true', PREVIEW_ONLY_REASON);

      await openApp(page, frontendUrl, path, deploymentSse);

      await armConnectRefusal(page, backendUrl, 401);
      await auth.forceError(401);
      const loginRequest = page.waitForRequest(request => request.url().includes('/api/auth/login'));

      await forceReconnect(page);
      await expectRejected(page, 401);

      expect((await loginRequest).url()).toContain('redirect=');
    });

    test('shows the connection-lost toast when the session check fails', async ({
      page,
      frontendUrl,
      backendUrl,
      auth,
      deploymentSse,
      toastHelper,
    }) => {
      test.skip(path.sharedWorker && process.env.PLAYWRIGHT_PREVIEW_MODE !== 'true', PREVIEW_ONLY_REASON);

      await openApp(page, frontendUrl, path, deploymentSse);

      await armConnectRefusal(page, backendUrl, 403);
      await auth.forceError(500);

      await forceReconnect(page);
      await expectRejected(page, 403);

      await toastHelper.expectErrorToast(/refresh the page/i);

      // The stream stays closed: no new stream within 3 s, and not connected.
      const reconnected = await waitForSseEvent(page, {
        streamId: 'connection',
        phase: 'open',
        event: 'connected',
        timeoutMs: 3000,
      }).then(
        () => true,
        () => false
      );
      expect(reconnected).toBe(false);
      expect((await deploymentSse.getStatus()).isConnected).toBe(false);
    });
  });
}
