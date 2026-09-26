import { useCallback, useEffect, useRef } from 'react';
import {
  trackFormError,
  trackFormOpen,
  trackFormSubmit,
  trackFormSuccess,
  trackFormValidationError,
  trackFormValidationErrors,
} from '@/lib/test/form-instrumentation';

interface UseFormInstrumentationOptions<TFields extends Record<string, unknown>> {
  formId: string;
  isOpen: boolean;
  snapshotFields?: () => TFields;
}

export interface UseFormInstrumentationResult<TFields extends Record<string, unknown>> {
  trackOpen: (fields?: TFields) => void;
  trackSubmit: (fields?: TFields) => void;
  trackSuccess: (fields?: TFields) => void;
  trackError: (fields?: TFields) => void;
  trackValidationError: (field: string, error: string, fields?: TFields) => void;
  trackValidationErrors: (errors: Record<string, string | undefined>, fields?: TFields) => void;
}

export function useFormInstrumentation<TFields extends Record<string, unknown> = Record<string, unknown>>({
  formId,
  isOpen,
  snapshotFields,
}: UseFormInstrumentationOptions<TFields>): UseFormInstrumentationResult<TFields> {
  const lastIsOpenRef = useRef(false);
  const lastFormIdRef = useRef<string | null>(null);
  const snapshotRef = useRef<typeof snapshotFields>(snapshotFields);

  useEffect(() => {
    snapshotRef.current = snapshotFields;
  }, [snapshotFields]);

  const resolveFields = useCallback(
    (fields?: TFields): TFields | undefined => {
      if (fields) {
        return fields;
      }
      const snapshot = snapshotRef.current;
      return snapshot ? snapshot() : undefined;
    },
    []
  );

  const trackOpenEvent = useCallback(
    (fields?: TFields) => {
      trackFormOpen(formId, resolveFields(fields));
    },
    [formId, resolveFields]
  );

  const trackSubmitEvent = useCallback(
    (fields?: TFields) => {
      trackFormSubmit(formId, resolveFields(fields));
    },
    [formId, resolveFields]
  );

  const trackSuccessEvent = useCallback(
    (fields?: TFields) => {
      trackFormSuccess(formId, resolveFields(fields));
    },
    [formId, resolveFields]
  );

  const trackErrorEvent = useCallback(
    (fields?: TFields) => {
      trackFormError(formId, resolveFields(fields));
    },
    [formId, resolveFields]
  );

  const trackValidationErrorEvent = useCallback(
    (field: string, error: string, fields?: TFields) => {
      trackFormValidationError(formId, field, error, resolveFields(fields));
    },
    [formId, resolveFields]
  );

  const trackValidationErrorsEvent = useCallback(
    (errors: Record<string, string | undefined>, fields?: TFields) => {
      trackFormValidationErrors(formId, errors, resolveFields(fields));
    },
    [formId, resolveFields]
  );

  useEffect(() => {
    if (!isOpen) {
      lastIsOpenRef.current = false;
      return;
    }

    const formChanged = lastFormIdRef.current !== formId;
    if (!lastIsOpenRef.current || formChanged) {
      trackOpenEvent();
      lastIsOpenRef.current = true;
      lastFormIdRef.current = formId;
    }
  }, [formId, isOpen, trackOpenEvent]);

  return {
    trackOpen: trackOpenEvent,
    trackSubmit: trackSubmitEvent,
    trackSuccess: trackSuccessEvent,
    trackError: trackErrorEvent,
    trackValidationError: trackValidationErrorEvent,
    trackValidationErrors: trackValidationErrorsEvent,
  };
}
