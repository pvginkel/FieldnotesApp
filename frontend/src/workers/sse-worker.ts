/**
 * SharedWorker for multiplexing a unified SSE connection across browser tabs.
 *
 * Maintains a single EventSource connection to the SSE endpoint and forwards
 * events to all connected tabs via MessagePort. The gateway sends all domain
 * events as unnamed data-only messages with a {type, payload} envelope, so the
 * worker uses a single `onmessage` handler to capture them. Two control signals
 * are named events: `ready` (no data) once the gateway has accepted the stream,
 * and `rejected` when the backend's connect callback refused it.
 *
 * The stream counts as connected on `ready`, not on `onopen`: a refused stream
 * opens too, before its `rejected` event.
 *
 * A `rejected` stream is terminal: the worker closes it and does not reconnect
 * until a tab sends `reconnect` (after refreshing the session).
 *
 * When the last tab disconnects, the worker closes the SSE connection.
 */

import { makeUniqueToken } from '@/lib/utils/random';

// Type definitions for worker messages

interface TestEventMetadata {
  kind: 'sse';
  streamId: string;
  phase: 'open' | 'message' | 'error' | 'close';
  event: string;
  data?: unknown;
}

type WorkerMessage =
  | { type: 'connected'; requestId: string; __testEvent?: TestEventMetadata }
  | { type: 'event'; event: string; data: unknown; __testEvent?: TestEventMetadata }
  | { type: 'disconnected'; reason?: string; __testEvent?: TestEventMetadata }
  | { type: 'error'; error: string; __testEvent?: TestEventMetadata }
  | { type: 'rejected'; status: number; message: string; __testEvent?: TestEventMetadata };

type TabCommand =
  | { type: 'connect'; isTestMode?: boolean }
  | { type: 'disconnect' }
  | { type: 'reconnect' };

// Worker state
const ports = new Set<MessagePort>();
const portTestModeMap = new WeakMap<MessagePort, boolean>();
let eventSource: EventSource | null = null;
let currentRequestId: string | null = null;
let retryCount = 0;
let retryTimeout: ReturnType<typeof setTimeout> | null = null;
const maxRetryDelay = 60000; // 60 seconds

// Cache the last version event so late-joining tabs receive it immediately
let lastVersionPayload: unknown = null;

// Whether the current stream has received the gateway's `ready` event. Tabs are
// told `connected` only then, late-joining tabs included.
let readyReceived = false;

// Set when the gateway sends `rejected`: keeps `onerror` from scheduling a
// reconnect until a tab sends `reconnect`. Cleared there and in
// `handleConnect()` when it opens a new stream.
let rejectedTerminal = false;

/**
 * Broadcast a message to all connected tabs
 */
function broadcast(message: WorkerMessage): void {
  ports.forEach(port => {
    try {
      port.postMessage(message);
    } catch (error) {
      console.error('SSE worker: Failed to post message to port:', error);
      ports.delete(port);
    }
  });
}

/**
 * Create test event metadata for instrumentation
 */
function createTestEvent(
  streamId: string,
  phase: 'open' | 'message' | 'error' | 'close',
  event: string,
  data?: unknown
): TestEventMetadata {
  return {
    kind: 'sse',
    streamId,
    phase,
    event,
    data,
  };
}

/**
 * Check if any connected port is in test mode
 */
function hasTestModePorts(): boolean {
  return Array.from(ports).some(port => portTestModeMap.get(port));
}

/**
 * Close the SSE connection and clean up resources
 */
function closeConnection(): void {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (retryTimeout) {
    clearTimeout(retryTimeout);
    retryTimeout = null;
  }
  currentRequestId = null;
  retryCount = 0;
  lastVersionPayload = null;
  readyReceived = false;
}

/**
 * Schedule reconnection with exponential backoff
 */
function scheduleReconnect(): void {
  if (ports.size === 0) {
    closeConnection();
    return;
  }

  if (retryTimeout) {
    clearTimeout(retryTimeout);
    retryTimeout = null;
  }

  retryCount++;
  const delay = Math.min(1000 * Math.pow(2, retryCount - 1), maxRetryDelay);

  console.debug(`SSE worker: Scheduling reconnection in ${delay}ms (attempt ${retryCount})`);

  retryTimeout = setTimeout(() => {
    if (ports.size > 0) {
      createEventSource();
    }
  }, delay);
}

/**
 * Create EventSource connection to unified SSE endpoint
 */
function createEventSource(): void {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }

  readyReceived = false;

  if (!currentRequestId) {
    currentRequestId = makeUniqueToken(32);
  }

  const params = new URLSearchParams({ request_id: currentRequestId });
  const url = `/api/sse/stream?${params.toString()}`;

  console.debug(`SSE worker: Creating EventSource for requestId=${currentRequestId}`);

  eventSource = new EventSource(url);

  eventSource.onopen = () => {
    // Open is not connected: the gateway may still refuse the stream. Tabs hear
    // `connected` on the `ready` event.
    console.debug('SSE worker: SSE connection opened (waiting for ready)');
    retryCount = 0;
  };

  // Single onmessage handler captures all envelope events from the gateway.
  // Each message contains {type, payload} — unwrap and broadcast to tabs.
  eventSource.onmessage = (e: MessageEvent) => {
    let envelope: { type: string; payload: unknown };
    try {
      envelope = JSON.parse(e.data);
    } catch {
      // Non-JSON data on the unnamed channel; ignore
      console.debug('SSE worker: Ignoring non-JSON unnamed event:', e.data);
      return;
    }

    if (!envelope.type) {
      return;
    }

    const eventName = envelope.type;
    const eventData = envelope.payload;

    // Cache the latest version payload for late-joining tabs
    if (eventName === 'version') {
      lastVersionPayload = eventData;
    }

    const message: WorkerMessage = {
      type: 'event',
      event: eventName,
      data: eventData,
    };

    if (hasTestModePorts()) {
      message.__testEvent = createTestEvent(eventName, 'message', eventName, eventData);
    }

    broadcast(message);
  };

  // The gateway sends `ready` as a named event with no data once it has
  // accepted the stream. Don't read `event.data` here.
  eventSource.addEventListener('ready', () => {
    readyReceived = true;

    const message: WorkerMessage = {
      type: 'connected',
      requestId: currentRequestId!,
    };

    if (hasTestModePorts()) {
      message.__testEvent = createTestEvent('connection', 'open', 'connected', {
        requestId: currentRequestId,
      });
    }

    broadcast(message);
  });

  // The gateway sends `rejected` as a named event with a JSON payload
  // (`{"status": <code>, "message": "<text>"}`) when the backend's connect
  // callback refused the stream (401/403), then ends the stream. Terminal:
  // close the EventSource, stop retrying, and tell every tab. The tabs refresh
  // the session through /api/auth/self and send `reconnect` when it is valid.
  eventSource.addEventListener('rejected', (event) => {
    let status = 0;
    let message = 'Unknown';
    const rawData: unknown = (event as MessageEvent).data;
    try {
      const payload = JSON.parse(String(rawData)) as {
        status?: number;
        message?: string;
      };
      if (typeof payload?.status === 'number') {
        status = payload.status;
      }
      if (typeof payload?.message === 'string') {
        message = payload.message;
      }
    } catch (parseError) {
      // Still terminal, with status 0. Keep the raw payload (truncated) for triage.
      const raw = String(rawData);
      const truncated = raw.length > 200 ? `${raw.slice(0, 200)}…` : raw;
      console.warn('SSE worker: Failed to parse `rejected` payload:', parseError, 'raw:', truncated);
    }

    rejectedTerminal = true;
    closeConnection();

    const rejectedMessage: WorkerMessage = {
      type: 'rejected',
      status,
      message,
    };

    if (hasTestModePorts()) {
      rejectedMessage.__testEvent = createTestEvent('connection', 'message', 'rejected', {
        status,
        message,
      });
    }

    broadcast(rejectedMessage);
  });

  eventSource.onerror = (event) => {
    // The stream's end after `rejected` can still fire `onerror`; the recovery
    // is the tabs' to drive, so don't schedule a reconnect.
    if (rejectedTerminal) {
      return;
    }

    console.error('SSE worker: EventSource error:', event);

    const errorMessage: WorkerMessage = {
      type: 'error',
      error: 'SSE connection error',
    };

    if (hasTestModePorts()) {
      errorMessage.__testEvent = createTestEvent('connection', 'error', 'error', {
        error: 'SSE connection error',
      });
    }

    broadcast(errorMessage);

    scheduleReconnect();
  };
}

/**
 * Handle tab connection
 */
function handleConnect(port: MessagePort, isTestMode = false): void {
  console.debug(`SSE worker: Tab connected (${ports.size + 1} active ports)`);

  if (isTestMode) {
    portTestModeMap.set(port, true);
  }

  ports.add(port);

  if (!eventSource) {
    // A new stream must not inherit an earlier rejection (the last tab left
    // after `rejected`, the worker stayed alive, and a new tab connects).
    rejectedTerminal = false;
    createEventSource();
  } else if (readyReceived) {
    // SSE already connected and ready — send connected message to new tab
    const connectedMessage: WorkerMessage = {
      type: 'connected',
      requestId: currentRequestId!,
    };

    if (isTestMode) {
      connectedMessage.__testEvent = createTestEvent('connection', 'open', 'connected', {
        requestId: currentRequestId,
      });
    }

    try {
      port.postMessage(connectedMessage);

      // Send cached version event so late-joining tabs get the current version
      if (lastVersionPayload !== null) {
        const versionMessage: WorkerMessage = {
          type: 'event',
          event: 'version',
          data: lastVersionPayload,
        };

        if (isTestMode) {
          versionMessage.__testEvent = createTestEvent('version', 'message', 'version', lastVersionPayload);
        }

        port.postMessage(versionMessage);
      }
    } catch (error) {
      console.error('SSE worker: Failed to send connected message to new tab:', error);
      ports.delete(port);
    }
  }
}

/**
 * Handle a tab's `reconnect` command: close any stream (which clears the retry
 * timer, request ID, cached version and ready flag), clear the rejection, and
 * open a fresh stream with a new request ID. The worker is single-threaded, so
 * commands that arrive together end in one stream.
 */
function handleReconnect(): void {
  closeConnection();
  rejectedTerminal = false;
  createEventSource();
}

/**
 * Handle tab disconnection
 */
function handleDisconnect(port: MessagePort): void {
  ports.delete(port);
  portTestModeMap.delete(port);

  console.debug(`SSE worker: Tab disconnected (${ports.size} active ports)`);

  if (ports.size === 0) {
    console.debug('SSE worker: Last tab disconnected, closing SSE connection');
    closeConnection();
  }
}

/**
 * SharedWorker onconnect handler
 */
self.addEventListener('connect', (event) => {
  const messageEvent = event as MessageEvent;
  const port = messageEvent.ports[0];

  port.onmessage = (messageEvent: MessageEvent<TabCommand>) => {
    const command = messageEvent.data;

    switch (command.type) {
      case 'connect':
        handleConnect(port, command.isTestMode);
        break;

      case 'disconnect':
        handleDisconnect(port);
        break;

      case 'reconnect':
        handleReconnect();
        break;

      default:
        console.warn('SSE worker: Unknown command:', command);
    }
  };

  port.addEventListener('messageerror', () => {
    console.debug('SSE worker: Port message error, removing port');
    handleDisconnect(port);
  });

  port.start();
});

console.debug('SSE worker: Initialized');
