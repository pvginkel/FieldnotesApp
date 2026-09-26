/**
 * Test event emission system
 * Provides structured event emission for test infrastructure
 */

import { isTestMode } from '@/lib/config/test-mode';
import type { TestEvent, TestEventPayload, TestEventKind } from '@/lib/test/test-events';
import { TestEventKind as EventKind } from '@/lib/test/test-events';

/**
 * Validate event kind against defined types
 */
function validateEventKind(kind: string): kind is TestEventKind {
  return Object.values(EventKind).includes(kind as TestEventKind);
}

/**
 * Emit a test event
 * In test mode: forwards to the Playwright bridge when present
 * In production: no-op function
 */
export function emitTestEvent(payload: TestEventPayload): void {
  if (!isTestMode()) {
    return; // No-op in production
  }

  // Validate event kind
  if (!validateEventKind(payload.kind)) {
    throw new Error(`Invalid test event kind: ${payload.kind}`);
  }

  // Create full event with timestamp
  const event = {
    ...payload,
    timestamp: new Date().toISOString(),
  } as TestEvent;

  // Forward to Playwright binding when available
  if (typeof window !== 'undefined') {
    const binding = window.__playwright_emitTestEvent;
    if (typeof binding === 'function') {
      const result = binding(event);
      if (result && typeof (result as Promise<unknown>).catch === 'function') {
        void (result as Promise<unknown>).catch(() => {
          /* swallow binding errors to avoid polluting console */
        });
      }
    }
  }
}
