import { useState, useEffect, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import { useToast } from '@/hooks/use-toast';
import { api } from '@/lib/api/generated/client';
import { isTestMode } from '@/lib/config/test-mode';
import { emitTestEvent } from '@/lib/test/event-emitter';
import type { SseTestEvent } from '@/lib/test/test-events';
import { makeUniqueToken } from '@/lib/utils/random';
import { SseContext } from './sse-context-base';
import type { SseContextValue } from './sse-context-base';
// Import to ensure test bridge registration (even though module is not used in production)
import '@/lib/config/sse-request-id';

interface SseContextProviderProps {
  children: ReactNode;
}

// Shown when a refused stream can't be recovered.
const CONNECTION_LOST_MESSAGE =
  'Connection to the server was lost. Please refresh the page to reconnect.';

// Worker message types; they mirror the union in `src/workers/sse-worker.ts`.
type WorkerMessage =
  | { type: 'connected'; requestId: string; __testEvent?: SseTestEventMetadata }
  | { type: 'event'; event: string; data: unknown; __testEvent?: SseTestEventMetadata }
  | { type: 'disconnected'; reason?: string; __testEvent?: SseTestEventMetadata }
  | { type: 'error'; error: string; __testEvent?: SseTestEventMetadata }
  | { type: 'rejected'; status: number; message: string; __testEvent?: SseTestEventMetadata };

interface SseTestEventMetadata {
  kind: 'sse';
  streamId: string;
  phase: 'open' | 'message' | 'error' | 'close';
  event: string;
  data?: unknown;
}

type SessionProbeOutcome = 'valid' | 'signed-out' | 'failed';

/**
 * Check the session after the backend refused the stream. GET /api/auth/self
 * refreshes the session cookies while the refresh token is good, so `valid`
 * means a new stream will be accepted. On `signed-out` (401) the API client's
 * middleware is already sending the user to login.
 */
async function probeSession(): Promise<SessionProbeOutcome> {
  try {
    const { response } = await api.GET('/api/auth/self', {
      credentials: 'include',
    });
    if (response.ok) {
      return 'valid';
    }
    return response.status === 401 ? 'signed-out' : 'failed';
  } catch (error) {
    console.error('SSE recovery: /api/auth/self failed:', error);
    return 'failed';
  }
}

/**
 * Read the backend's `rejected` payload, `{"status": <code>, "message": "<text>"}`.
 * A payload that doesn't parse gives status 0.
 */
function parseRejection(data: unknown): { status: number; message: string } {
  try {
    const payload = JSON.parse(String(data)) as { status?: unknown; message?: unknown };
    return {
      status: typeof payload?.status === 'number' ? payload.status : 0,
      message: typeof payload?.message === 'string' ? payload.message : 'Unknown',
    };
  } catch {
    return { status: 0, message: 'Unknown' };
  }
}

/**
 * Determine if SharedWorker should be used based on environment
 */
function shouldUseSharedWorker(): boolean {
  // Always use direct connection in development mode
  if (import.meta.env.DEV) {
    return false;
  }

  // In test mode, only use SharedWorker if explicitly enabled via URL parameter
  if (isTestMode()) {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      return params.has('__sharedWorker');
    }
    return false;
  }

  // Check if SharedWorker is supported (graceful fallback for iOS Safari)
  if (typeof SharedWorker === 'undefined') {
    return false;
  }

  // Production mode with SharedWorker support
  return true;
}

// Decided once, when the module loads: in test mode the opt-in is the
// `__sharedWorker` query parameter of the URL the page loaded with, and a route
// that redirects on load (an index route, say) drops it before the provider
// mounts.
const USE_SHARED_WORKER = shouldUseSharedWorker();

/**
 * SSE context provider that manages a unified SSE connection.
 *
 * Exposes a generic addEventListener interface. Consumer hooks subscribe to
 * named SSE events and handle their own DTO parsing.
 *
 * The backend sends all domain events as unnamed data-only messages with a
 * {type, payload} envelope. Both the SharedWorker and the direct EventSource
 * path unwrap the envelope and dispatch to local listeners by event name.
 *
 * Two control signals are named events. `ready` (no data) means the backend
 * accepted the stream; only then is the connection `isConnected`, since a
 * refused stream opens too. `rejected` means the backend refused it
 * (401/403): both paths close the stream instead of retrying it, check the
 * session, and open a new stream once the session is valid again.
 *
 * Uses SharedWorker in production for cross-tab connection sharing, falls back to
 * direct EventSource in dev/test/iOS environments.
 */
export function SseContextProvider({ children }: SseContextProviderProps) {
  const [isConnected, setIsConnected] = useState(false);
  const [requestId, setRequestId] = useState<string | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const sharedWorkerRef = useRef<SharedWorker | null>(null);
  const useSharedWorker = useRef<boolean>(USE_SHARED_WORKER);
  // The direct path's recovery reopens through this ref, since
  // createDirectConnection can't refer to itself.
  const createDirectConnectionRef = useRef<(() => void) | null>(null);

  // Internal listener registry: Map<eventName, Set<handler>>
  const listenersRef = useRef(new Map<string, Set<(data: unknown) => void>>());

  // showError through a ref: ToastProvider builds a new context value on every
  // render, so depending on showError would rebuild handleWorkerMessage, and
  // with it the SharedWorker, on every toast. Requires ToastProvider above this
  // provider (CoreProviders wraps SseProviders).
  const { showError } = useToast();
  const showErrorRef = useRef(showError);
  useEffect(() => {
    showErrorRef.current = showError;
  }, [showError]);

  /**
   * Recover from a refused stream: check the session, then `reopen` when it is
   * valid, leave the redirect to login to the API client when it is gone, and
   * otherwise tell the user to refresh the page.
   */
  const recoverFromRejection = useCallback((status: number, reopen: () => void) => {
    // Only the status: the message comes from the backend's response.
    console.debug(`SSE stream rejected: status=${status}`);
    setIsConnected(false);

    void probeSession().then(outcome => {
      if (outcome === 'valid') {
        reopen();
      } else if (outcome === 'failed') {
        showErrorRef.current(CONNECTION_LOST_MESSAGE);
      }
    });
  }, []);

  /**
   * Dispatch an event to all registered handlers for a given event name
   */
  const dispatchEvent = useCallback((eventName: string, data: unknown) => {
    const handlers = listenersRef.current.get(eventName);
    if (handlers) {
      for (const handler of handlers) {
        try {
          handler(data);
        } catch (error) {
          console.error(`Error in SSE event handler for '${eventName}':`, error);
        }
      }
    }
  }, []);

  /**
   * Register a handler for a named SSE event.
   * Purely local — no subscription commands are sent to the worker or EventSource.
   * Returns an unsubscribe function.
   */
  const addEventListener = useCallback(
    (event: string, handler: (data: unknown) => void): (() => void) => {
      const map = listenersRef.current;
      if (!map.has(event)) {
        map.set(event, new Set());
      }
      map.get(event)!.add(handler);

      return () => {
        const handlers = map.get(event);
        if (handlers) {
          handlers.delete(handler);
          if (handlers.size === 0) {
            map.delete(event);
          }
        }
      };
    },
    []
  );

  /**
   * Handle worker messages and dispatch to listeners
   */
  const handleWorkerMessage = useCallback((message: WorkerMessage) => {
    // Forward test events if present
    if (message.__testEvent && isTestMode()) {
      emitTestEvent(message.__testEvent);
    }

    switch (message.type) {
      case 'connected':
        setIsConnected(true);
        setRequestId(message.requestId);
        break;

      case 'event':
        dispatchEvent(message.event, message.data);
        break;

      case 'error':
        console.error('SSE connection error:', message.error);
        setIsConnected(false);
        break;

      case 'disconnected':
        console.debug('SSE disconnected:', message.reason);
        setIsConnected(false);
        break;

      case 'rejected':
        // The worker has closed the stream and won't retry it until told to.
        // Post to the worker directly, not through reconnect(), so the
        // recovery doesn't depend on the isConnected guard or render timing.
        recoverFromRejection(message.status, () => {
          sharedWorkerRef.current?.port.postMessage({ type: 'reconnect' });
        });
        break;

      default:
        console.warn('Unknown worker message:', message);
    }
  }, [dispatchEvent, recoverFromRejection]);

  /**
   * Create SharedWorker connection
   */
  const createSharedWorkerConnection = useCallback(() => {
    try {
      console.debug('SSE context: Using SharedWorker for unified SSE connection');

      const worker = new SharedWorker(
        new URL('../workers/sse-worker.ts', import.meta.url),
        { type: 'module' }
      );

      sharedWorkerRef.current = worker;

      worker.port.onmessage = (event: MessageEvent<WorkerMessage>) => {
        handleWorkerMessage(event.data);
      };

      worker.port.start();
      worker.port.postMessage({
        type: 'connect',
        isTestMode: isTestMode(),
      });
    } catch (error) {
      console.error('Failed to create SharedWorker, falling back to direct connection:', error);
      useSharedWorker.current = false;
      setIsConnected(false);
    }
  }, [handleWorkerMessage]);

  /**
   * Create direct EventSource connection.
   * Uses onmessage to unwrap the {type, payload} envelope — same logic as the SharedWorker.
   */
  const createDirectConnection = useCallback(() => {
    const tabRequestId = makeUniqueToken(32);

    const params = new URLSearchParams({ request_id: tabRequestId });
    const url = `/api/sse/stream?${params}`;

    const eventSource = new EventSource(url);
    eventSourceRef.current = eventSource;

    // Open is not connected: the backend may still refuse the stream. The
    // connection counts from the `ready` event (no data line; don't read it).
    eventSource.addEventListener('ready', () => {
      setIsConnected(true);
      setRequestId(tabRequestId);

      if (isTestMode()) {
        const payload: Omit<SseTestEvent, 'timestamp'> = {
          kind: 'sse',
          streamId: 'connection',
          phase: 'open',
          event: 'connected',
          data: { requestId: tabRequestId },
        };
        emitTestEvent(payload);
      }
    });

    // The backend refused the stream and is ending it. Close it before the
    // browser retries it, and open a new one only once the session is valid,
    // unless this stream was replaced or disconnected meanwhile.
    eventSource.addEventListener('rejected', (event) => {
      const { status, message } = parseRejection((event as MessageEvent).data);
      eventSource.close();

      if (isTestMode()) {
        emitTestEvent({
          kind: 'sse',
          streamId: 'connection',
          phase: 'message',
          event: 'rejected',
          data: { status, message },
        });
      }

      recoverFromRejection(status, () => {
        if (eventSourceRef.current === eventSource) {
          createDirectConnectionRef.current?.();
        }
      });
    });

    // Single onmessage handler unwraps the {type, payload} envelope
    // and dispatches to local listeners — mirrors the SharedWorker logic.
    eventSource.onmessage = (e: MessageEvent) => {
      let envelope: { type: string; payload: unknown };
      try {
        envelope = JSON.parse(e.data);
      } catch {
        // Non-JSON data on the unnamed channel; ignore
        return;
      }

      if (!envelope.type) {
        return;
      }

      const eventName = envelope.type;
      const eventData = envelope.payload;

      if (isTestMode()) {
        emitTestEvent({
          kind: 'sse',
          streamId: eventName,
          phase: 'message',
          event: eventName,
          data: eventData,
        });
      }

      dispatchEvent(eventName, eventData);
    };

    eventSource.onerror = (event) => {
      if (isTestMode()) {
        console.debug('EventSource error (test mode):', event);
      } else {
        console.error('EventSource error:', event);
      }
      setIsConnected(false);
    };
  }, [dispatchEvent, recoverFromRejection]);

  useEffect(() => {
    createDirectConnectionRef.current = createDirectConnection;
  }, [createDirectConnection]);

  /**
   * Disconnect from SSE
   */
  const disconnect = useCallback(() => {
    if (sharedWorkerRef.current) {
      try {
        sharedWorkerRef.current.port.postMessage({ type: 'disconnect' });
        sharedWorkerRef.current.port.close();
      } catch (error) {
        console.error('Failed to disconnect SharedWorker:', error);
      }
      sharedWorkerRef.current = null;
    }

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    setIsConnected(false);
  }, []);

  /**
   * Reconnect SSE (for focus-based reconnection).
   *
   * A healthy connection is left alone, on both paths, so that a focus event
   * doesn't tear it down. The recovery from a refused stream reopens it without
   * going through here.
   */
  const reconnect = useCallback(() => {
    if (isConnected) {
      return;
    }

    if (useSharedWorker.current) {
      if (sharedWorkerRef.current) {
        // The worker is alive but its stream is down: ask it to reconnect.
        sharedWorkerRef.current.port.postMessage({ type: 'reconnect' });
        return;
      }
      createSharedWorkerConnection();
      return;
    }

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    createDirectConnection();
  }, [isConnected, createSharedWorkerConnection, createDirectConnection]);

  // Establish connection on mount
  useEffect(() => {
    if (useSharedWorker.current) {
      createSharedWorkerConnection();
    } else {
      createDirectConnection();
    }

    return () => {
      disconnect();
    };
  }, [createSharedWorkerConnection, createDirectConnection, disconnect]);

  // Test-only bridge: `window.__sseForceReconnect()` opens a new stream even
  // while the connection is healthy, which reconnect() refuses to do, so a spec
  // can make the next connect meet a refusal it has armed. It posts `reconnect`
  // to the SharedWorker, or replaces the direct EventSource. It isn't part of
  // SseContextValue, and it doesn't exist outside test mode.
  useEffect(() => {
    if (!isTestMode()) {
      return;
    }

    if (typeof window === 'undefined') {
      return;
    }

    const bridge = () => {
      if (useSharedWorker.current) {
        sharedWorkerRef.current?.port.postMessage({ type: 'reconnect' });
        return;
      }
      eventSourceRef.current?.close();
      createDirectConnection();
    };

    window.__sseForceReconnect = bridge;

    return () => {
      if (window.__sseForceReconnect === bridge) {
        delete window.__sseForceReconnect;
      }
    };
  }, [createDirectConnection]);

  const contextValue: SseContextValue = {
    isConnected,
    requestId,
    addEventListener,
    reconnect,
  };

  return (
    <SseContext.Provider value={contextValue}>
      {children}
    </SseContext.Provider>
  );
}
