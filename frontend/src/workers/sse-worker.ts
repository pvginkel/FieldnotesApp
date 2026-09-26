/**
 * SharedWorker for multiplexing a unified SSE connection across browser tabs.
 *
 * Maintains a single EventSource connection to the SSE endpoint and forwards
 * events to all connected tabs via MessagePort. The gateway sends all events as
 * unnamed data-only messages with a {type, payload} envelope, so the worker
 * uses a single `onmessage` handler to capture everything.
 *
 * When the last tab disconnects, the worker closes the SSE connection.
 */

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
  | { type: 'error'; error: string; __testEvent?: TestEventMetadata };

type TabCommand =
  | { type: 'connect'; isTestMode?: boolean }
  | { type: 'disconnect' };

// Token generation utility (inline version of makeUniqueToken)
const TOKEN_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789';
function makeUniqueToken(length = 32): string {
  let token = '';
  for (let i = 0; i < length; i++) {
    token += TOKEN_CHARS.charAt(Math.floor(Math.random() * TOKEN_CHARS.length));
  }
  return token;
}

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

  if (!currentRequestId) {
    currentRequestId = makeUniqueToken(32);
  }

  const params = new URLSearchParams({ request_id: currentRequestId });
  const url = `/api/sse/stream?${params.toString()}`;

  console.debug(`SSE worker: Creating EventSource for requestId=${currentRequestId}`);

  eventSource = new EventSource(url);

  eventSource.onopen = () => {
    console.debug('SSE worker: SSE connection opened');
    retryCount = 0;

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

  // connection_close stays as a named event (internal plumbing)
  eventSource.addEventListener('connection_close', (event) => {
    try {
      const data = JSON.parse(event.data);
      console.debug('SSE worker: Connection closed by backend:', data.reason);
    } catch {
      // Ignore parse errors for connection_close
    }

    closeConnection();
  });

  eventSource.onerror = (event) => {
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
    createEventSource();
  } else if (eventSource.readyState === EventSource.OPEN) {
    // SSE already connected — send connected message to new tab
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
