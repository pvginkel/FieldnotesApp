/**
 * Task SSE event tests verify that task events are correctly routed
 * from the unified SSE stream to subscribers via the test event system.
 *
 * These tests use the /api/testing/sse/task-event endpoint to inject
 * deterministic task events without running actual background tasks.
 */
import { test, expect } from '../../support/fixtures';
import { makeUnique } from '../../support/helpers';
import { waitForSseEvent, extractSseData } from '../../support/helpers/test-events';

// The SSE context provider emits connection events with streamId 'connection'
// and task events arrive as envelope type 'task_event'.
const CONNECTION_STREAM_ID = 'connection';
const TASK_STREAM_ID = 'task_event';

test.describe('Task SSE events', () => {
  test('receives task events through unified SSE stream', async ({
    page,
    frontendUrl,
    backendUrl,
    testEvents,
    deploymentSse,
  }) => {
    // Navigate to page
    await page.goto(frontendUrl);
    await testEvents.clearEvents();

    // Establish SSE connection using the standard pattern
    await deploymentSse.resetRequestId();
    const connectionStatus = await deploymentSse.ensureConnected();

    // Wait for SSE connection event
    const connectedEvent = await waitForSseEvent(page, {
      streamId: CONNECTION_STREAM_ID,
      phase: 'open',
      event: 'connected',
      timeoutMs: 15000,
    });

    const connectionData = extractSseData<{ requestId?: string }>(connectedEvent);
    const requestId = connectionStatus.requestId ?? connectionData?.requestId;
    expect(requestId).toBeTruthy();
    if (!requestId) {
      return;
    }

    // Generate a unique task ID
    const taskId = makeUnique('test-task');

    // Send a task event through the testing endpoint
    const response = await page.request.post(`${backendUrl}/api/testing/sse/task-event`, {
      data: {
        request_id: requestId,
        task_id: taskId,
        event_type: 'progress_update',
        data: {
          text: 'Processing test data...',
          value: 0.5,
        },
      },
    });

    expect(response.ok()).toBe(true);

    // Verify the task event was received.
    // The gateway wraps the event as {"type":"task_event","payload":{...}},
    // so the streamId and event are both 'task_event'. The inner event_type
    // lives inside the payload data.
    const taskEvent = await waitForSseEvent(page, {
      streamId: TASK_STREAM_ID,
      phase: 'message',
      event: TASK_STREAM_ID,
      matcher: (event) => {
        const payload = extractSseData<{ task_id?: string }>(event);
        return payload?.task_id === taskId;
      },
      timeoutMs: 5000,
    });

    expect(taskEvent).toBeTruthy();
    const taskPayload = extractSseData<{
      task_id: string;
      event_type: string;
      data: unknown;
    }>(taskEvent);
    expect(taskPayload?.task_id).toBe(taskId);
    expect(taskPayload?.event_type).toBe('progress_update');

    await deploymentSse.disconnect();
  });

  test('task events include correct payload structure', async ({
    page,
    frontendUrl,
    backendUrl,
    testEvents,
    deploymentSse,
  }) => {
    await page.goto(frontendUrl);
    await testEvents.clearEvents();

    await deploymentSse.resetRequestId();
    const connectionStatus = await deploymentSse.ensureConnected();

    await waitForSseEvent(page, {
      streamId: CONNECTION_STREAM_ID,
      phase: 'open',
      event: 'connected',
      timeoutMs: 15000,
    });

    const requestId = connectionStatus.requestId;
    expect(requestId).toBeTruthy();
    if (!requestId) {
      return;
    }

    const taskId = makeUnique('payload-test');

    // Send task_started event
    await page.request.post(`${backendUrl}/api/testing/sse/task-event`, {
      data: {
        request_id: requestId,
        task_id: taskId,
        event_type: 'task_started',
        data: { message: 'Analysis started' },
      },
    });

    const startEvent = await waitForSseEvent(page, {
      streamId: TASK_STREAM_ID,
      phase: 'message',
      event: TASK_STREAM_ID,
      matcher: (event) => {
        const payload = extractSseData<{ task_id?: string; event_type?: string }>(event);
        return payload?.task_id === taskId && payload?.event_type === 'task_started';
      },
      timeoutMs: 5000,
    });

    const startPayload = extractSseData<{
      task_id: string;
      event_type: string;
      data: { message: string };
    }>(startEvent);
    expect(startPayload?.event_type).toBe('task_started');
    expect(startPayload?.data?.message).toBe('Analysis started');

    // Send task_completed event
    await page.request.post(`${backendUrl}/api/testing/sse/task-event`, {
      data: {
        request_id: requestId,
        task_id: taskId,
        event_type: 'task_completed',
        data: {
          result: { status: 'success' },
        },
      },
    });

    const completeEvent = await waitForSseEvent(page, {
      streamId: TASK_STREAM_ID,
      phase: 'message',
      event: TASK_STREAM_ID,
      matcher: (event) => {
        const payload = extractSseData<{ task_id?: string; event_type?: string }>(event);
        return payload?.task_id === taskId && payload?.event_type === 'task_completed';
      },
      timeoutMs: 5000,
    });

    const completePayload = extractSseData<{
      task_id: string;
      event_type: string;
      data: { result: unknown };
    }>(completeEvent);
    expect(completePayload?.event_type).toBe('task_completed');
    expect(completePayload?.data?.result).toEqual({ status: 'success' });

    await deploymentSse.disconnect();
  });

  test('multiple task events are received in sequence', async ({
    page,
    frontendUrl,
    backendUrl,
    testEvents,
    deploymentSse,
  }) => {
    await page.goto(frontendUrl);
    await testEvents.clearEvents();

    await deploymentSse.resetRequestId();
    const connectionStatus = await deploymentSse.ensureConnected();

    await waitForSseEvent(page, {
      streamId: CONNECTION_STREAM_ID,
      phase: 'open',
      event: 'connected',
      timeoutMs: 15000,
    });

    const requestId = connectionStatus.requestId;
    expect(requestId).toBeTruthy();
    if (!requestId) {
      return;
    }

    const taskId = makeUnique('sequence-test');

    // Send sequence of events
    const events = [
      { event_type: 'task_started', data: { message: 'Starting' } },
      { event_type: 'progress_update', data: { text: 'Step 1', value: 0.25 } },
      { event_type: 'progress_update', data: { text: 'Step 2', value: 0.50 } },
      { event_type: 'progress_update', data: { text: 'Step 3', value: 0.75 } },
      { event_type: 'task_completed', data: { result: 'done' } },
    ];

    for (const event of events) {
      await page.request.post(`${backendUrl}/api/testing/sse/task-event`, {
        data: {
          request_id: requestId,
          task_id: taskId,
          ...event,
        },
      });

      // Wait for each event to be received — all arrive with streamId 'task_event'.
      // Discriminate by matching on task_id and event_type within the payload.
      await waitForSseEvent(page, {
        streamId: TASK_STREAM_ID,
        phase: 'message',
        event: TASK_STREAM_ID,
        matcher: (e) => {
          const payload = extractSseData<{ task_id?: string; event_type?: string }>(e);
          return payload?.task_id === taskId && payload?.event_type === event.event_type;
        },
        timeoutMs: 5000,
      });
    }

    await deploymentSse.disconnect();
  });
});
