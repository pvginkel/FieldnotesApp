/**
 * Router instrumentation for test events
 * Hooks into TanStack Router to emit navigation events
 */

import type { AnyRouter } from '@tanstack/react-router';
import { emitTestEvent } from './event-emitter';
import { TestEventKind, type RouteTestEvent } from '@/lib/test/test-events';

/**
 * Setup router instrumentation to emit route test-event payloads
 */
export function setupRouterInstrumentation(router: AnyRouter): () => void {
  // Subscribe to router navigation events using TanStack Router's subscription API
  const unsubscribe = router.subscribe('onResolved', (event) => {
    // Extract navigation information from the resolved event
    const fromPath = event.fromLocation
      ? event.fromLocation.pathname + (event.fromLocation.searchStr || '')
      : '';
    const toPath = event.toLocation.pathname + (event.toLocation.searchStr || '');

    const routeEvent: Omit<RouteTestEvent, 'timestamp'> = {
      kind: TestEventKind.ROUTE,
      from: fromPath,
      to: toPath,
      params: (event.toLocation as { params?: Record<string, string> }).params || {},
    };

    emitTestEvent(routeEvent);
  });

  // Return cleanup function
  return unsubscribe;
}
