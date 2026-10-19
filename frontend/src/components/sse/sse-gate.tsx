/**
 * SseGate component.
 *
 * Renders its children at once; it no longer waits for the SSE connection.
 * The connection counts only from the backend's `ready` event, so blocking
 * here would leave a blank page for as long as the stream is slow or down.
 * The app works without SSE until then: listeners registered before `ready`
 * still receive every event the stream delivers, and `useSSETask` buffers task
 * events from mount.
 *
 * Kept as a placeholder so the provider tree stays stable.
 */

import type { ReactNode } from 'react';

interface SseGateProps {
  children: ReactNode;
}

export function SseGate({ children }: SseGateProps) {
  return <>{children}</>;
}
