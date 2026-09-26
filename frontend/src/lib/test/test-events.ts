/**
 * TypeScript interfaces for test event types
 * Used by the test event emission system
 */

/**
 * Test event kinds
 */
export const TestEventKind = {
  ROUTE: 'route',
  FORM: 'form',
  API: 'api',
  TOAST: 'toast',
  ERROR: 'error',
  QUERY_ERROR: 'query_error',
  UI_STATE: 'ui_state',
  SSE: 'sse',
  LIST_LOADING: 'list_loading',
} as const;

export type TestEventKind = typeof TestEventKind[keyof typeof TestEventKind];

/**
 * Base interface for all test events
 */
export interface BaseTestEvent {
  kind: TestEventKind;
  timestamp: string;
}

/**
 * Route navigation event
 */
export interface RouteTestEvent extends BaseTestEvent {
  kind: 'route';
  from: string;
  to: string;
  params?: Record<string, string>;
}

/**
 * Form interaction event
 */
export interface FormTestEvent extends BaseTestEvent {
  kind: 'form';
  phase: 'open' | 'submit' | 'success' | 'error' | 'validation_error';
  formId: string;
  fields?: Record<string, unknown>;
  metadata?: {
    field?: string;
    error?: string;
    [key: string]: unknown;
  };
}

/**
 * API request event
 */
export interface ApiTestEvent extends BaseTestEvent {
  kind: 'api';
  operation: string;
  method: string;
  status: number;
  correlationId: string;
  durationMs: number;
}

/**
 * Toast notification event
 */
export interface ToastTestEvent extends BaseTestEvent {
  kind: 'toast';
  level: 'success' | 'error' | 'warning' | 'info';
  code?: string;
  message: string;
  action?: string;
  exception?: {
    name?: string;
    message?: string;
    stack?: string;
  };
}

/**
 * General error event
 */
export interface ErrorTestEvent extends BaseTestEvent {
  kind: 'error';
  scope: string;
  code?: string;
  message: string;
  correlationId?: string;
}

/**
 * Query error event (React Query specific)
 */
export interface QueryErrorTestEvent extends BaseTestEvent {
  kind: 'query_error';
  queryKey: string;
  status?: number;
  message: string;
  correlationId?: string;
  metadata?: Record<string, unknown>;
}

/**
 * Server-sent event
 */
export interface SseTestEvent extends BaseTestEvent {
  kind: 'sse';
  streamId: string;
  phase: 'open' | 'message' | 'error' | 'close';
  event: string;
  data?: unknown;
}

/**
 * UI state lifecycle event (loading/ready)
 */
export interface UiStateTestEvent extends BaseTestEvent {
  kind: 'ui_state';
  scope: string;
  phase: 'loading' | 'ready' | 'open' | 'submit' | 'success' | 'error' | 'forbidden';
  metadata?: Record<string, unknown>;
}

/**
 * List loading lifecycle event (start/ready/error/aborted)
 * Also supports custom phases like 'filtered' and 'cleared' for search operations
 */
export interface ListLoadingTestEvent extends BaseTestEvent {
  kind: 'list_loading';
  scope: string;
  phase: 'loading' | 'ready' | 'error' | 'aborted' | 'filtered' | 'cleared';
  metadata?: {
    paginationInfo?: {
      pagesFetched: number;
      limit: number;
    };
    [key: string]: unknown;
  };
}

/**
 * Union type for all test events
 */
export type TestEvent =
  | RouteTestEvent
  | FormTestEvent
  | ApiTestEvent
  | ToastTestEvent
  | ErrorTestEvent
  | QueryErrorTestEvent
  | UiStateTestEvent
  | ListLoadingTestEvent
  | SseTestEvent;

/**
 * Distributive Omit that preserves union discriminants.
 * Unlike built-in Omit which collapses union members to shared keys only.
 */
type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

/**
 * Test event payload - the actual data passed to emitTestEvent
 */
export type TestEventPayload = DistributiveOmit<TestEvent, 'timestamp'>;
