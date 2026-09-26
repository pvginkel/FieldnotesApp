import type { RouteTestEvent, ToastTestEvent } from '@/lib/test/test-events';
import { test, expect } from '../support/fixtures-infrastructure';
import { emitTestEvent } from '../support/helpers/test-events';
import { expectConsoleError, waitTestEvent } from '../support/helpers';

const ROUTE_EVENT_KIND = 'route';

test.describe('Playwright bridge instrumentation', () => {
  test('captures route events emitted by the app', async ({ page, frontendUrl, testEvents }) => {
    await page.goto(frontendUrl);

    const routeEvent = await testEvents.waitForEvent(event => event.kind === ROUTE_EVENT_KIND);

    expect(routeEvent.kind).toBe(ROUTE_EVENT_KIND);
    const typedRouteEvent = routeEvent as RouteTestEvent;
    expect(typedRouteEvent.to).toContain('/');
  });

  test('waitTestEvent helper reads from the Playwright buffer', async ({ page, frontendUrl }) => {
    await page.goto(frontendUrl);

    const routeEvent = (await waitTestEvent(page, ROUTE_EVENT_KIND)) as RouteTestEvent;
    expect(routeEvent.kind).toBe(ROUTE_EVENT_KIND);
  });

  test('binding is exposed to the browser context', async ({ page, frontendUrl }) => {
    await page.goto(frontendUrl);

    const hasBinding = await page.evaluate(() => {
      const globalAny = window as typeof window & { __playwright_emitTestEvent?: unknown };
      return typeof globalAny.__playwright_emitTestEvent === 'function';
    });

    expect(hasBinding).toBe(true);
  });

  test('synthetic emitTestEvent plumbing delivers payloads to the buffer', async ({ page, frontendUrl, testEvents }) => {
    await page.goto(frontendUrl);

    const toastPayload: Omit<ToastTestEvent, 'timestamp'> = {
      kind: 'toast',
      level: 'info',
      message: 'bridge-synthetic-event',
    };

    await emitTestEvent(page, toastPayload);

    const toastEvent = await testEvents.waitForEvent(event => {
      return event.kind === 'toast' && event.message === 'bridge-synthetic-event';
    });

    const typedToastEvent = toastEvent as ToastTestEvent;
    expect(typedToastEvent.message).toBe('bridge-synthetic-event');
  });

  test('showException instrumentation exposes exception details to Playwright', async ({ page, frontendUrl, testEvents }) => {
    await page.goto(frontendUrl);

    await expectConsoleError(page, /Toast exception/);

    await page.evaluate(() => {
      const error = new Error('toast exception wiring');
      window.dispatchEvent(
        new CustomEvent('app:testing:show-exception', {
          detail: { message: 'toast exception integration', error },
        })
      );
    });

    const toastEvent = await testEvents.waitForEvent(event => {
      return event.kind === 'toast' && event.message === 'toast exception integration';
    });

    const typedToastEvent = toastEvent as ToastTestEvent;
    expect(typedToastEvent.exception?.message).toContain('toast exception wiring');
    expect(typedToastEvent.exception?.stack).toContain('toast exception wiring');
  });
});
