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
}

/**
 * Hook that emits `ui_state` events when a widget transitions between loading and ready/error.
 */
export function useUiStateInstrumentation(
  scope: string,
  options: UiStateInstrumentationOptions,
): void {
  const { isLoading, error, getReadyMetadata, getErrorMetadata } = options;
  const activeRef = useRef(false);
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
    }
  }, [error, isLoading, scope, testMode]);

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
