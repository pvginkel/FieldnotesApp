import { useState, useEffect, useCallback, useRef } from 'react';
import { isTestMode } from '@/lib/config/test-mode';
import { emitTestEvent } from '@/lib/test/event-emitter';
import type { SseTestEvent } from '@/lib/test/test-events';
import { useSseContext } from '@/contexts/sse-context';

/**
 * Raw task event data as received from the SSE stream.
 * The SSE plumbing forwards this as-is; parsing happens here.
 */
interface RawTaskEvent {
  task_id: string;
  event_type: string;
  data: unknown;
}

interface SSEProgressEvent {
  event_type: 'progress_update';
  data: {
    text: string;
    value?: number;
  };
}

interface SSEResultEvent {
  event_type: 'task_completed';
  data: {
    success: boolean;
    error_message: string | null;
    [key: string]: unknown;
  };
}

interface SSEErrorEvent {
  event_type: 'task_failed';
  data: {
    error: string;
    code?: string;
  };
}

interface SSEStartedEvent {
  event_type: 'task_started';
  data: null;
}

type SSEEvent = SSEProgressEvent | SSEResultEvent | SSEErrorEvent | SSEStartedEvent;

interface UseSSETaskOptions {
  onProgress?: (message: string, percentage?: number) => void;
  onResult?: <T>(data: T) => void;
  onError?: (message: string, code?: string) => void;
}

interface UseSSETaskReturn<T> {
  subscribeToTask: (taskId: string) => void;
  unsubscribe: () => void;
  isSubscribed: boolean;
  error: string | null;
  result: T | null;
  progress: {
    message: string;
    percentage?: number;
  } | null;
}

// Buffer recent task events to handle race conditions.
// When the backend responds very quickly, events may arrive before the client
// has finished subscribing. This buffer allows late subscribers to replay events.
const TASK_BUFFER_TTL_MS = 10000;
const taskEventBuffer = new Map<string, { events: RawTaskEvent[]; timestamp: number }>();

function bufferTaskEvent(event: RawTaskEvent): void {
  // eslint-disable-next-line no-restricted-properties -- Date.now() used for TTL timing, not ID generation
  const now = Date.now();

  // Clean up old buffered events
  for (const [id, data] of taskEventBuffer.entries()) {
    if (now - data.timestamp > TASK_BUFFER_TTL_MS) {
      taskEventBuffer.delete(id);
    }
  }

  const taskId = event.task_id;
  if (!taskEventBuffer.has(taskId)) {
    taskEventBuffer.set(taskId, { events: [], timestamp: now });
  }
  const buffer = taskEventBuffer.get(taskId)!;
  buffer.events.push(event);
  buffer.timestamp = now;
}

export function useSSETask<T = unknown>(options: UseSSETaskOptions = {}): UseSSETaskReturn<T> {
  const [isSubscribed, setIsSubscribed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<T | null>(null);
  const [progress, setProgress] = useState<{ message: string; percentage?: number } | null>(null);

  const currentTaskIdRef = useRef<string | null>(null);
  const unsubscribeListenerRef = useRef<(() => void) | null>(null);

  const {
    onProgress,
    onResult,
    onError,
  } = options;

  const { addEventListener } = useSseContext();

  const unsubscribe = useCallback(() => {
    if (unsubscribeListenerRef.current) {
      unsubscribeListenerRef.current();
      unsubscribeListenerRef.current = null;
    }
    currentTaskIdRef.current = null;
    setIsSubscribed(false);
  }, []);

  /**
   * Process a single task event through the state machine
   */
  const processEvent = useCallback((taskId: string, parsedEvent: SSEEvent) => {
    switch (parsedEvent.event_type) {
      case 'task_started': {
        if (isTestMode()) {
          const payload: Omit<SseTestEvent, 'timestamp'> = {
            kind: 'sse',
            streamId: 'task',
            phase: 'message',
            event: 'task_started',
            data: { taskId },
          };
          emitTestEvent(payload);
        }
        break;
      }

      case 'progress_update': {
        const progressData = {
          message: parsedEvent.data.text,
          percentage: parsedEvent.data.value
        };
        setProgress(progressData);
        onProgress?.(progressData.message, progressData.percentage);

        if (isTestMode()) {
          const payload: Omit<SseTestEvent, 'timestamp'> = {
            kind: 'sse',
            streamId: 'task',
            phase: 'message',
            event: 'progress_update',
            data: { taskId, ...progressData },
          };
          emitTestEvent(payload);
        }
        break;
      }

      case 'task_completed': {
        if (parsedEvent.data.success && !parsedEvent.data.error_message) {
          setResult(parsedEvent.data as T);
          onResult?.(parsedEvent.data as T);

          if (isTestMode()) {
            const payload: Omit<SseTestEvent, 'timestamp'> = {
              kind: 'sse',
              streamId: 'task',
              phase: 'message',
              event: 'task_completed',
              data: { taskId, success: true },
            };
            emitTestEvent(payload);
          }
        } else {
          const errorMessage = parsedEvent.data.error_message || 'Task failed';
          setError(errorMessage);
          onError?.(errorMessage);

          if (isTestMode()) {
            const payload: Omit<SseTestEvent, 'timestamp'> = {
              kind: 'sse',
              streamId: 'task',
              phase: 'error',
              event: 'task_completed',
              data: { taskId, success: false, error: errorMessage },
            };
            emitTestEvent(payload);
          }
        }
        unsubscribe();
        break;
      }

      case 'task_failed': {
        const errorMessage = parsedEvent.data.error;
        const errorCode = parsedEvent.data.code;
        setError(errorMessage);
        onError?.(errorMessage, errorCode);

        if (isTestMode()) {
          const payload: Omit<SseTestEvent, 'timestamp'> = {
            kind: 'sse',
            streamId: 'task',
            phase: 'error',
            event: 'task_failed',
            data: { taskId, error: errorMessage, code: errorCode },
          };
          emitTestEvent(payload);
        }

        unsubscribe();
        break;
      }
    }
  }, [onProgress, onResult, onError, unsubscribe]);

  const subscribeToTask = useCallback((taskId: string) => {
    // Unsubscribe from any previous task
    unsubscribe();

    // Reset state for new subscription
    setError(null);
    setResult(null);
    setProgress(null);
    setIsSubscribed(true);
    currentTaskIdRef.current = taskId;

    // Emit test event for subscription start
    if (isTestMode()) {
      const payload: Omit<SseTestEvent, 'timestamp'> = {
        kind: 'sse',
        streamId: 'task',
        phase: 'open',
        event: 'task_subscription',
        data: { taskId },
      };
      emitTestEvent(payload);
    }

    // Replay any buffered events for this task
    const buffer = taskEventBuffer.get(taskId);
    if (buffer) {
      for (const rawEvent of buffer.events) {
        const parsedEvent = {
          event_type: rawEvent.event_type,
          data: rawEvent.data,
        } as SSEEvent;
        processEvent(taskId, parsedEvent);
      }
    }

    // Subscribe to live task_event stream, filtered by taskId
    const unsubscribeListener = addEventListener('task_event', (data: unknown) => {
      try {
        const rawEvent = data as RawTaskEvent;

        // Buffer all task events for late-subscriber replay
        bufferTaskEvent(rawEvent);

        // Only process events for our task
        if (rawEvent.task_id !== taskId) return;

        const parsedEvent = {
          event_type: rawEvent.event_type,
          data: rawEvent.data,
        } as SSEEvent;

        processEvent(taskId, parsedEvent);
      } catch (parseError) {
        console.error('Failed to parse SSE task event:', parseError);
        setError('Invalid server response format');
      }
    });

    unsubscribeListenerRef.current = unsubscribeListener;
  }, [addEventListener, processEvent, unsubscribe]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      unsubscribe();
    };
  }, [unsubscribe]);

  return {
    subscribeToTask,
    unsubscribe,
    isSubscribed,
    error,
    result,
    progress
  };
}
