import { useContext } from 'react';
import { SseContext } from './sse-context-base';

/**
 * Hook to access SSE context
 *
 * Must be used within SseContextProvider
 */
export function useSseContext() {
  const context = useContext(SseContext);

  if (!context) {
    throw new Error('useSseContext must be used within SseContextProvider');
  }

  return context;
}
