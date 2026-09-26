import type { TestEvent, UiStateTestEvent, ListLoadingTestEvent } from '@/lib/test/test-events';
import { makeUniqueToken as baseMakeUniqueToken } from '@/lib/utils/random';
import { Page, expect } from '@playwright/test';
import { getTestEventBuffer } from './helpers/test-events';

export function makeUniqueToken(length: number = 6): string {
  return baseMakeUniqueToken(length);
}

/**
 * Generates a pseudo-random identifier with prefix-shortId pattern for test data.
 */
export function makeUnique(prefix: string = 'test', length: number = 6): string {
  const suffix = makeUniqueToken(length);
  if (!prefix) {
    return suffix;
  }
  return `${prefix}-${suffix}`;
}

/**
 * Waits for a test-event payload exposed through the Playwright bridge
 * @param page The Playwright page instance
 * @param kind The event kind to wait for
 * @param filter Optional filter function to match specific event payloads
 * @param timeout Maximum time to wait for the event
 * @returns The event data payload
 */
export async function waitTestEvent<T = TestEvent>(
  page: Page,
  kind: string,
  filter?: (payload: T) => boolean,
  timeout = 10_000
): Promise<T> {
  const buffer = getTestEventBuffer(page);
  const matchedEvent = await buffer.waitForEvent((event) => {
    if (event.kind !== kind) {
      return false;
    }
    if (!filter) {
      return true;
    }
    return filter(event as unknown as T);
  }, timeout);

  return matchedEvent as unknown as T;
}

export async function waitForUiState(
  page: Page,
  scope: string,
  phase: UiStateTestEvent['phase'],
  timeout = 10_000
): Promise<UiStateTestEvent> {
  return waitTestEvent<UiStateTestEvent>(
    page,
    'ui_state',
    event => event.scope === scope && event.phase === phase,
    timeout,
  );
}

export async function waitForListLoading(
  page: Page,
  scope: string,
  phase: 'loading' | 'ready' | 'error' | 'aborted' | 'filtered' | 'cleared',
  timeout = 10_000,
): Promise<ListLoadingTestEvent> {
  return waitTestEvent<ListLoadingTestEvent>(
    page,
    'list_loading',
    event => event.scope === scope && event.phase === phase,
    timeout,
  );
}

/**
 * Waits for an element to be visible and returns it
 */
export async function waitForElement(page: Page, selector: string, timeout: number = 10000) {
  await expect(page.locator(selector)).toBeVisible({ timeout });
  return page.locator(selector);
}

/**
 * Waits for a form validation error event
 * @param page The Playwright page instance
 * @param formId The form ID to match
 * @param field Optional field name to match
 * @param timeout Maximum time to wait for the event
 * @returns The validation error event data
 */
export async function waitForFormValidationError(
  page: Page,
  formId: string,
  field?: string,
  timeout = 10000
): Promise<any> {
  return waitTestEvent(page, 'form', (event: any) => {
    return event.phase === 'validation_error' &&
           event.formId === formId &&
           (!field || event.metadata?.field === field);
  }, timeout);
}

/**
 * Waits for a 409 conflict error event
 * @param page The Playwright page instance
 * @param correlationId Optional correlation ID to match
 * @param timeout Maximum time to wait for the event
 * @returns The conflict error event data
 */
export async function expectConflictError(
  page: Page,
  correlationId?: string,
  timeout = 10000
): Promise<any> {
  return waitTestEvent(page, 'query_error', (event: any) => {
    return event.status === 409 &&
           (!correlationId || event.correlationId === correlationId);
  }, timeout);
}

/**
 * Registers a console error pattern as expected for the current test
 * This prevents the test from failing when the specified console error occurs
 * @param page The Playwright page instance
 * @param pattern Regular expression pattern to match against console errors
 */
export async function expectConsoleError(page: Page, pattern: RegExp): Promise<void> {
  const source = pattern.source;
  const flags = pattern.flags;
  const includeToastPrefix = !/toast exception/i.test(source);

  await page.evaluate(
    ({ source, flags, includeToastPrefix }) => {
      // @ts-expect-error - This function is exposed by our fixture
      window.__registerExpectedError(source, flags);
      if (includeToastPrefix) {
        const combined = `Toast exception[\\s\\S]*${source}`;
        // @ts-expect-error - This function is exposed by our fixture
        window.__registerExpectedError(combined, flags);
      }
    },
    { source, flags, includeToastPrefix }
  );
}

