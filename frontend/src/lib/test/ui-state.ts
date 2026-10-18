import { useEffect, useRef } from 'react';
import type { UiStateTestEvent } from '@/lib/test/test-events';
import { isTestMode } from '@/lib/config/test-mode';
import { emitTestEvent } from './event-emitter';

type UiStatePayload = Omit<UiStateTestEvent, 'timestamp'>;
type UiStateMetadata = Record<string, unknown> | undefined;

export function beginUiState(scope: string): void {
  if (!isTestMode()) {
    return;
  }

  const payload: UiStatePayload = {
    kind: 'ui_state',
    scope,
    phase: 'loading',
  };

  emitTestEvent(payload);
}

export function endUiState(scope: string, metadata?: UiStateMetadata): void {
  if (!isTestMode()) {
    return;
  }

  const payload: UiStatePayload = {
    kind: 'ui_state',
    scope,
    phase: 'ready',
    ...(metadata ? { metadata } : {}),
  };

  emitTestEvent(payload);
}

interface UiStateInstrumentationOptions {
  isLoading: boolean;
  error?: unknown;
  getReadyMetadata?: () => UiStateMetadata;
  getErrorMetadata?: (error: unknown) => UiStateMetadata;
  /**
   * Emit `ready` on mount when the scope isn't loading (default true). That
   * suits a data scope, whose data may be cached at mount, so a loading ->
   * ready transition never comes. An action scope, whose `isLoading` follows
   * a mutation or another user action, sets it to false: it has nothing to
   * be ready about at mount, and the test-event buffer would hand that
   * metadata-less `ready` to a waiter registered later for the action's own.
   */
  readyOnMount?: boolean;
}

/**
 * Hook that emits `ui_state` events when a widget transitions between loading and ready/error.
 */
export function useUiStateInstrumentation(
  scope: string,
  options: UiStateInstrumentationOptions,
): void {
  const { isLoading, error, getReadyMetadata, getErrorMetadata, readyOnMount = true } = options;
  const activeRef = useRef(false);
  const mountedRef = useRef(false);
  const readyMetadataRef = useRef(getReadyMetadata);
  const errorMetadataRef = useRef(getErrorMetadata);
  const testMode = isTestMode();

  useEffect(() => {
    readyMetadataRef.current = getReadyMetadata;
  }, [getReadyMetadata]);

  useEffect(() => {
    errorMetadataRef.current = getErrorMetadata;
  }, [getErrorMetadata]);

  useEffect(() => {
    if (!testMode) {
      activeRef.current = false;
      return;
    }

    const isFirstRun = !mountedRef.current;
    mountedRef.current = true;

    if (isLoading && !activeRef.current) {
      beginUiState(scope);
      activeRef.current = true;
      return;
    }

    if (!isLoading && activeRef.current) {
      activeRef.current = false;
      const hasError = Boolean(error);
      const metadata = hasError
        ? errorMetadataRef.current?.(error)
        : readyMetadataRef.current?.();
      endUiState(scope, metadata);
      return;
    }

    // Data was already available on mount (e.g. route loader pre-cached) —
    // emit ready immediately so Playwright waiters don't hang waiting for a
    // loading -> ready transition that will never happen. Not for an action
    // scope (readyOnMount: false), which is idle at mount, not ready.
    if (isFirstRun && !isLoading && readyOnMount) {
      const hasError = Boolean(error);
      const metadata = hasError
        ? errorMetadataRef.current?.(error)
        : readyMetadataRef.current?.();
      endUiState(scope, metadata);
    }
  }, [error, isLoading, readyOnMount, scope, testMode]);

  useEffect(() => {
    if (!testMode) {
      return undefined;
    }

    return () => {
      if (!activeRef.current) {
        return;
      }

      activeRef.current = false;
      const metadata = errorMetadataRef.current?.(error);
      endUiState(scope, {
        ...(metadata ?? {}),
        status: 'aborted',
      });
    };
  }, [error, scope, testMode]);
}
