/**
 * Form instrumentation for test events
 * Provides utilities to track form lifecycle events
 */

import React from 'react';
import { emitTestEvent } from './event-emitter';
import { TestEventKind, type FormTestEvent } from '@/lib/test/test-events';
import { isTestMode } from '@/lib/config/test-mode';

/**
 * Generate a stable form ID based on component name and optional identifier
 */
export function generateFormId(componentName: string, identifier?: string): string {
  const baseId = componentName.toLowerCase().replace(/form$/, '');
  return identifier ? `${baseId}_${identifier}` : baseId;
}

/**
 * Track form open event
 */
export function trackFormOpen(formId: string, fields?: Record<string, unknown>): void {
  if (!isTestMode()) return;

  const formEvent: Omit<FormTestEvent, 'timestamp'> = {
    kind: TestEventKind.FORM,
    phase: 'open',
    formId,
    ...(fields && { fields }),
  };

  emitTestEvent(formEvent);
}

/**
 * Track form submit event
 */
export function trackFormSubmit(formId: string, fields?: Record<string, unknown>): void {
  if (!isTestMode()) return;

  const snapshot = fields ? { ...fields } : undefined;
  const formEvent: Omit<FormTestEvent, 'timestamp'> = {
    kind: TestEventKind.FORM,
    phase: 'submit',
    formId,
    ...(snapshot && { fields: snapshot, metadata: snapshot }),
  };

  emitTestEvent(formEvent);
}

/**
 * Track form success event
 */
export function trackFormSuccess(formId: string, fields?: Record<string, unknown>): void {
  if (!isTestMode()) return;

  const snapshot = fields ? { ...fields } : undefined;
  const formEvent: Omit<FormTestEvent, 'timestamp'> = {
    kind: TestEventKind.FORM,
    phase: 'success',
    formId,
    ...(snapshot && { fields: snapshot, metadata: snapshot }),
  };

  emitTestEvent(formEvent);
}

/**
 * Track form error event
 */
export function trackFormError(formId: string, fields?: Record<string, unknown>): void {
  if (!isTestMode()) return;

  const snapshot = fields ? { ...fields } : undefined;
  const formEvent: Omit<FormTestEvent, 'timestamp'> = {
    kind: TestEventKind.FORM,
    phase: 'error',
    formId,
    ...(snapshot && { fields: snapshot, metadata: snapshot }),
  };

  emitTestEvent(formEvent);
}

/**
 * Track form validation error event
 */
export function trackFormValidationError(
  formId: string,
  fieldName: string,
  errorMessage: string,
  fields?: Record<string, unknown>
): void {
  if (!isTestMode()) return;

  const formEvent: Omit<FormTestEvent, 'timestamp'> = {
    kind: TestEventKind.FORM,
    phase: 'validation_error',
    formId,
    ...(fields && { fields }),
    metadata: {
      field: fieldName,
      error: errorMessage,
    },
  };

  emitTestEvent(formEvent);
}

/**
 * Track all validation errors from a validation result
 * This is a convenience function for tracking multiple validation errors at once
 */
export function trackFormValidationErrors(
  formId: string,
  errors: Record<string, string | undefined>,
  fields?: Record<string, unknown>
): void {
  if (!isTestMode()) return;

  for (const [field, error] of Object.entries(errors)) {
    if (error) {
      trackFormValidationError(formId, field, error, fields);
    }
  }
}

/**
 * Higher-order component wrapper for form tracking
 * This is a utility function that can be used to wrap form components
 */
export function withFormTracking<T extends Record<string, unknown>>(
  Component: React.ComponentType<T>,
  formName: string
) {
  return function TrackedFormComponent(props: T) {
    const formId = generateFormId(formName);

    // Track form open on mount
    React.useEffect(() => {
      trackFormOpen(formId);
    }, [formId]);

    return React.createElement(Component, props);
  };
}
