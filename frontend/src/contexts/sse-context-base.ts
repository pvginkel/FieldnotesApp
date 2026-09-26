import { createContext } from 'react';

/**
 * SSE context value exposed to consumers.
 *
 * Provides a generic addEventListener interface for subscribing to named SSE events.
 * Consumer hooks are responsible for parsing event data into their own DTOs.
 */
export interface SseContextValue {
  isConnected: boolean;
  requestId: string | null;
  /** Subscribe to a named SSE event; returns an unsubscribe function */
  addEventListener: (event: string, handler: (data: unknown) => void) => () => void;
  reconnect: () => void;
}

/**
 * Default context value (throws when accessed outside provider)
 */
export const defaultSseContextValue: SseContextValue = {
  isConnected: false,
  requestId: null,
  addEventListener: () => {
    throw new Error('useSseContext must be used within SseContextProvider');
  },
  reconnect: () => {
    throw new Error('useSseContext must be used within SseContextProvider');
  },
};

/**
 * SSE context for unified stream management
 */
export const SseContext = createContext<SseContextValue>(defaultSseContextValue);
