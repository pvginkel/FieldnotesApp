/**
 * SSE connectivity and event delivery tests.
 *
 * Verifies that:
 * - The SSE connection goes through the Vite dev server proxy (no direct gateway access)
 * - Version events are delivered correctly through the React SSE infrastructure
 * - Task events are routed through the Vite proxy to the browser (envelope format)
 * - SharedWorker path delivers events correctly when enabled via URL parameter
 */
import { test, expect } from '../../support/fixtures';
import { makeUnique } from '../../support/helpers';
import { extractSseData, waitForSseEvent } from '../../support/helpers/test-events';

test.describe('SSE connectivity', () => {
  test('connects through the Vite proxy, not directly to the gateway', async ({
    page,
    frontendUrl,
    gatewayUrl,
    deploymentSse,
  }) => {
    await page.goto(frontendUrl);

    await deploymentSse.resetRequestId();
    await deploymentSse.ensureConnected();

    await waitForSseEvent(page, {
      streamId: 'connection',
      phase: 'open',
      event: 'connected',
      timeoutMs: 15000,
    });

    // Inspect all resource entries to verify SSE URLs go through the frontend
    // origin (Vite proxy) and not directly to the gateway.
    const sseUrls = await page.evaluate(() => {
      const entries = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
      return entries
        .filter(e => e.initiatorType === 'other' && e.name.includes('/api/sse/'))
        .map(e => e.name);
    });

    for (const url of sseUrls) {
      expect(url).not.toContain(gatewayUrl);
      expect(url).toContain('/api/sse/stream');
    }

    await deploymentSse.disconnect();
  });

  test('delivers version events from the SSE stream', async ({
    page,
    frontendUrl,
    backendUrl,
    deploymentSse,
  }) => {
    await page.goto(frontendUrl);

    await deploymentSse.resetRequestId();
    const connectionStatus = await deploymentSse.ensureConnected();

    await waitForSseEvent(page, {
      streamId: 'connection',
      phase: 'open',
      event: 'connected',
      timeoutMs: 15000,
    });

    const requestId = connectionStatus.requestId;
    expect(requestId).toBeTruthy();

    const versionLabel = makeUnique('version-test');

    const response = await page.request.post(`${backendUrl}/api/testing/deployments/version`, {
      data: {
        request_id: requestId,
        version: versionLabel,
      },
    });
    expect(response.ok()).toBeTruthy();

    const versionEvent = await waitForSseEvent(page, {
      streamId: 'version',
      phase: 'message',
      event: 'version',
      matcher: event => {
        const payload = extractSseData<{ version?: string }>(event);
        return payload?.version === versionLabel;
      },
      timeoutMs: 15000,
    });

    const payload = extractSseData<{ version: string }>(versionEvent);
    expect(payload?.version).toBe(versionLabel);

    await deploymentSse.disconnect();
  });

  test('routes task events through the Vite proxy', async ({
    page,
    frontendUrl,
    sseTimeout,
  }) => {
    await page.goto(frontendUrl);

    const taskId = makeUnique('task');

    // Create a standalone EventSource in the browser. This opens a separate
    // SSE connection through the Vite proxy, registers with the SSE Gateway,
    // and listens for task_event messages via the envelope format.
    // The gateway now sends all events as unnamed data-only messages wrapping
    // {type, payload}, so we use es.onmessage and filter by type.
    const receivedEvent = await page.evaluate(
      async ({ tId, timeout }) => {
        // Generate a unique request_id for this connection
        const requestId = 'pw-' + Math.random().toString(36).substring(2, 15);
        const params = new URLSearchParams({ request_id: requestId });
        const es = new EventSource(`/api/sse/stream?${params}`);

        // Set up the envelope message listener BEFORE waiting for open,
        // so no events are missed between open and send.
        const eventPromise = new Promise<unknown>((resolve, reject) => {
          const timer = setTimeout(() => {
            es.close();
            reject(new Error('task_event timeout'));
          }, timeout);

          es.onmessage = (e: MessageEvent) => {
            try {
              const envelope = JSON.parse(e.data);
              if (envelope.type === 'task_event' && envelope.payload?.task_id === tId) {
                clearTimeout(timer);
                es.close();
                resolve(envelope.payload);
              }
            } catch {
              // Ignore parse errors
            }
          };
        });

        // Wait for the EventSource to open (connection registered with Gateway)
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('EventSource open timeout')), timeout);
          es.addEventListener('open', () => { clearTimeout(timer); resolve(); });
          es.addEventListener('error', () => {
            if (es.readyState === EventSource.CLOSED) {
              clearTimeout(timer);
              reject(new Error('EventSource closed during open'));
            }
          });
        });

        // Small delay to ensure the Gateway callback has registered the connection
        await new Promise(resolve => setTimeout(resolve, 500));

        // Send a fake task event to our request_id via the backend testing API.
        // Uses relative URL so the request goes through the Vite proxy.
        const sendResponse = await fetch('/api/testing/sse/task-event', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            request_id: requestId,
            task_id: tId,
            event_type: 'progress_update',
            data: { text: 'Processing...', value: 0.5 },
          }),
        });

        if (!sendResponse.ok) {
          const err = await sendResponse.text();
          es.close();
          throw new Error(`Failed to send task event: ${sendResponse.status} ${err}`);
        }

        // Wait for the task event to arrive via SSE (envelope format)
        return eventPromise;
      },
      { tId: taskId, timeout: sseTimeout }
    );

    const event = receivedEvent as {
      task_id: string;
      event_type: string;
      data: { text: string; value: number };
    };
    expect(event.task_id).toBe(taskId);
    expect(event.event_type).toBe('progress_update');
    expect(event.data.text).toBe('Processing...');
    expect(event.data.value).toBe(0.5);
  });

  test('routes task failure events through the Vite proxy', async ({
    page,
    frontendUrl,
    sseTimeout,
  }) => {
    await page.goto(frontendUrl);

    const taskId = makeUnique('fail-task');

    const receivedEvent = await page.evaluate(
      async ({ tId, timeout }) => {
        const requestId = 'pw-' + Math.random().toString(36).substring(2, 15);
        const params = new URLSearchParams({ request_id: requestId });
        const es = new EventSource(`/api/sse/stream?${params}`);

        // Set up the envelope message listener BEFORE waiting for open,
        // so no events are missed between open and send.
        const eventPromise = new Promise<unknown>((resolve, reject) => {
          const timer = setTimeout(() => {
            es.close();
            reject(new Error('task_event timeout'));
          }, timeout);

          es.onmessage = (e: MessageEvent) => {
            try {
              const envelope = JSON.parse(e.data);
              if (envelope.type === 'task_event' && envelope.payload?.task_id === tId) {
                clearTimeout(timer);
                es.close();
                resolve(envelope.payload);
              }
            } catch {
              // Ignore parse errors
            }
          };
        });

        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('EventSource open timeout')), timeout);
          es.addEventListener('open', () => { clearTimeout(timer); resolve(); });
          es.addEventListener('error', () => {
            if (es.readyState === EventSource.CLOSED) {
              clearTimeout(timer);
              reject(new Error('EventSource closed during open'));
            }
          });
        });

        await new Promise(resolve => setTimeout(resolve, 500));

        const sendResponse = await fetch('/api/testing/sse/task-event', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            request_id: requestId,
            task_id: tId,
            event_type: 'task_failed',
            data: { error: 'proxy-failure-test', code: 'TEST_ERROR' },
          }),
        });

        if (!sendResponse.ok) {
          const err = await sendResponse.text();
          es.close();
          throw new Error(`Failed to send task event: ${sendResponse.status} ${err}`);
        }

        return eventPromise;
      },
      { tId: taskId, timeout: sseTimeout }
    );

    const event = receivedEvent as {
      task_id: string;
      event_type: string;
      data: { error: string; code: string };
    };
    expect(event.task_id).toBe(taskId);
    expect(event.event_type).toBe('task_failed');
    expect(event.data.error).toBe('proxy-failure-test');
    expect(event.data.code).toBe('TEST_ERROR');
  });

  test('delivers version events via SharedWorker', async ({
    page,
    frontendUrl,
    backendUrl,
    deploymentSse,
  }) => {
    // Navigate with __sharedWorker parameter to enable SharedWorker path
    await page.goto(`${frontendUrl}?__sharedWorker`);

    await deploymentSse.resetRequestId();
    const connectionStatus = await deploymentSse.ensureConnected();

    await waitForSseEvent(page, {
      streamId: 'connection',
      phase: 'open',
      event: 'connected',
      timeoutMs: 15000,
    });

    const requestId = connectionStatus.requestId;
    expect(requestId).toBeTruthy();

    const versionLabel = makeUnique('sw-version');

    const response = await page.request.post(`${backendUrl}/api/testing/deployments/version`, {
      data: {
        request_id: requestId,
        version: versionLabel,
      },
    });
    expect(response.ok()).toBeTruthy();

    const versionEvent = await waitForSseEvent(page, {
      streamId: 'version',
      phase: 'message',
      event: 'version',
      matcher: event => {
        const payload = extractSseData<{ version?: string }>(event);
        return payload?.version === versionLabel;
      },
      timeoutMs: 15000,
    });

    const payload = extractSseData<{ version: string }>(versionEvent);
    expect(payload?.version).toBe(versionLabel);

    await deploymentSse.disconnect();
  });

  test('connects and receives events via SharedWorker', async ({
    page,
    frontendUrl,
    gatewayUrl,
    deploymentSse,
  }) => {
    // Navigate with __sharedWorker parameter to enable SharedWorker path
    await page.goto(`${frontendUrl}?__sharedWorker`);

    await deploymentSse.resetRequestId();
    await deploymentSse.ensureConnected();

    await waitForSseEvent(page, {
      streamId: 'connection',
      phase: 'open',
      event: 'connected',
      timeoutMs: 15000,
    });

    // Verify SSE URLs go through the frontend (Vite proxy), not directly to the gateway
    const sseUrls = await page.evaluate(() => {
      const entries = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
      return entries
        .filter(e => e.initiatorType === 'other' && e.name.includes('/api/sse/'))
        .map(e => e.name);
    });

    for (const url of sseUrls) {
      expect(url).not.toContain(gatewayUrl);
      expect(url).toContain('/api/sse/stream');
    }

    await deploymentSse.disconnect();
  });
});
