/**
 * Query error instrumentation for test events
 * Hooks into React Query to emit query error events
 */

import { useEffect, useRef } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import { emitTestEvent } from './event-emitter';
import { TestEventKind, type QueryErrorTestEvent, type ListLoadingTestEvent } from '@/lib/test/test-events';
import { isTestMode } from '@/lib/config/test-mode';

/**
 * Setup query error instrumentation to emit query-error test-event payloads
 */
export function setupQueryInstrumentation(queryClient: QueryClient): void {
  // Only set up instrumentation in test mode
  if (!isTestMode()) {
    return;
  }

  // Set up query cache observer for queries
  const queryCache = queryClient.getQueryCache();
  queryCache.subscribe((event) => {
    if (event.type === 'updated' && event.query?.state.status === 'error') {
      const query = event.query;

      // Extract query information
      const queryKey = Array.isArray(query.queryKey)
        ? query.queryKey.join(':')
        : String(query.queryKey);

      // Extract HTTP status and correlation ID from error object
      let status: number | undefined;
      let correlationId: string | undefined;
      const error = query.state.error;

      if (error && typeof error === 'object') {
        // Check for status
        if ('status' in error && typeof error.status === 'number') {
          status = error.status;
        } else if ('response' in error && error.response && typeof error.response === 'object' && 'status' in error.response) {
          status = (error.response as { status: number }).status;
        }

        // Check for correlation ID
        if ('correlationId' in error && typeof error.correlationId === 'string') {
          correlationId = error.correlationId;
        } else if ('headers' in error && error.headers && typeof error.headers === 'object') {
          const headers = error.headers as Record<string, string>;
          correlationId = headers['x-correlation-id'] || headers['X-Correlation-Id'];
        }
      }

      // Prepare metadata for conflict errors
      const metadata: Record<string, unknown> = {};
      if (status === 409) {
        metadata.isConflict = true;
        if (error && typeof error === 'object' && 'data' in error) {
          metadata.conflictDetails = error.data;
        }
      }

      // Create and emit query error event
      const queryErrorEvent: Omit<QueryErrorTestEvent, 'timestamp'> = {
        kind: TestEventKind.QUERY_ERROR,
        queryKey,
        status,
        message: error instanceof Error ? error.message : String(error),
        correlationId,
        ...(Object.keys(metadata).length > 0 && { metadata }),
      };

      emitTestEvent(queryErrorEvent);
    }
  });

  // Set up mutation cache observer for mutations
  const mutationCache = queryClient.getMutationCache();
  mutationCache.subscribe((event) => {
    if (event.type === 'updated' && event.mutation?.state.status === 'error') {
      const mutation = event.mutation;

      // Extract mutation information
      const queryKey = `mutation:${mutation.options.mutationKey?.join(':') || 'unknown'}`;

      // Extract HTTP status and correlation ID from error object
      let status: number | undefined;
      let correlationId: string | undefined;
      const error = mutation.state.error;

      if (error && typeof error === 'object') {
        // Check for status
        if ('status' in error && typeof error.status === 'number') {
          status = error.status;
        } else if ('response' in error && error.response && typeof error.response === 'object' && 'status' in error.response) {
          status = (error.response as { status: number }).status;
        }

        // Check for correlation ID
        if ('correlationId' in error && typeof error.correlationId === 'string') {
          correlationId = error.correlationId;
        } else if ('headers' in error && error.headers && typeof error.headers === 'object') {
          const headers = error.headers as Record<string, string>;
          correlationId = headers['x-correlation-id'] || headers['X-Correlation-Id'];
        }
      }

      // Prepare metadata for conflict errors
      const metadata: Record<string, unknown> = {};
      if (status === 409) {
        metadata.isConflict = true;
        if (error && typeof error === 'object' && 'data' in error) {
          metadata.conflictDetails = error.data;
        }
      }

      // Create and emit query error event for mutations
      const queryErrorEvent: Omit<QueryErrorTestEvent, 'timestamp'> = {
        kind: TestEventKind.QUERY_ERROR,
        queryKey,
        status,
        message: error instanceof Error ? error.message : String(error),
        correlationId,
        ...(Object.keys(metadata).length > 0 && { metadata }),
      };

      emitTestEvent(queryErrorEvent);
    }
  });
}

interface ListLoadingInstrumentationOptions {
  scope: string;
  isLoading: boolean;
  isFetching?: boolean;
  error?: unknown;
  getReadyMetadata?: () => Record<string, unknown> | undefined;
  getErrorMetadata?: (error: unknown) => Record<string, unknown> | undefined;
  getAbortedMetadata?: () => Record<string, unknown> | undefined;
}

/**
 * Emit list loading lifecycle test events for components that rely on TanStack Query.
 * Provides deterministic hooks Playwright can await instead of intercepting requests.
 */
export function useListLoadingInstrumentation(options: ListLoadingInstrumentationOptions): void {
  const {
    scope,
    isLoading,
    isFetching = false,
    error,
    getReadyMetadata,
    getErrorMetadata,
    getAbortedMetadata,
  } = options;

  const testMode = isTestMode();
  const instrumentationActiveRef = useRef(false);
  const mountedRef = useRef(false);
  const readyMetadataRef = useRef(getReadyMetadata);
  const errorMetadataRef = useRef(getErrorMetadata);
  const abortedMetadataRef = useRef(getAbortedMetadata);

  useEffect(() => {
    readyMetadataRef.current = getReadyMetadata;
  }, [getReadyMetadata]);

  useEffect(() => {
    errorMetadataRef.current = getErrorMetadata;
  }, [getErrorMetadata]);

  useEffect(() => {
    abortedMetadataRef.current = getAbortedMetadata;
  }, [getAbortedMetadata]);

  useEffect(() => {
    if (!testMode) {
      instrumentationActiveRef.current = false;
      return;
    }

    const inFlight = isLoading || isFetching;
    const isFirstRun = !mountedRef.current;
    mountedRef.current = true;

    if (inFlight && !instrumentationActiveRef.current) {
      instrumentationActiveRef.current = true;
      const loadingPayload: Omit<ListLoadingTestEvent, 'timestamp'> = {
        kind: TestEventKind.LIST_LOADING,
        scope,
        phase: 'loading',
      };
      emitTestEvent(loadingPayload);
      return;
    }

    if (!inFlight && instrumentationActiveRef.current) {
      instrumentationActiveRef.current = false;

      const hasError = Boolean(error);
      const metadata = hasError
        ? errorMetadataRef.current?.(error)
        : readyMetadataRef.current?.();

      const completionPayload: Omit<ListLoadingTestEvent, 'timestamp'> = {
        kind: TestEventKind.LIST_LOADING,
        scope,
        phase: hasError ? 'error' : 'ready',
        ...(metadata ? { metadata } : {}),
      };

      emitTestEvent(completionPayload);
      return;
    }

    // Data was already available on mount (cached query) — emit ready immediately
    // so Playwright waiters don't hang waiting for a loading→ready transition.
    if (isFirstRun && !inFlight) {
      const hasError = Boolean(error);
      const metadata = hasError
        ? errorMetadataRef.current?.(error)
        : readyMetadataRef.current?.();

      const completionPayload: Omit<ListLoadingTestEvent, 'timestamp'> = {
        kind: TestEventKind.LIST_LOADING,
        scope,
        phase: hasError ? 'error' : 'ready',
        ...(metadata ? { metadata } : {}),
      };

      emitTestEvent(completionPayload);
    }
  }, [error, isFetching, isLoading, scope, testMode]);

  useEffect(() => {
    if (!testMode) {
      return undefined;
    }

    return () => {
      if (!instrumentationActiveRef.current) {
        return;
      }

      instrumentationActiveRef.current = false;
      const metadata = abortedMetadataRef.current?.();

      const abortedPayload: Omit<ListLoadingTestEvent, 'timestamp'> = {
        kind: TestEventKind.LIST_LOADING,
        scope,
        phase: 'aborted',
        ...(metadata ? { metadata } : {}),
      };

      emitTestEvent(abortedPayload);
    };
  }, [scope, testMode]);
}
