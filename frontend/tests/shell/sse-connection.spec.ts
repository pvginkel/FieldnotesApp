/**
 * The page's SSE connection (NFR-4): once the page has one, every request it makes to the API names
 * it, for the API to bind it to whoever the request authenticated as. The worker backend's fake
 * controller plays the inbox sitting's session and the actioner's run. Every item is invented.
 */

import type { Page, Request } from '@playwright/test';
import { test, expect } from '../support/fixtures';
import type { TestEventCapture } from '../support/helpers/test-events';
import { minutesAgo, stack } from '../support/triage-store';

/** The page's own SSE connection, as the page connected it. */
async function connection(testEvents: TestEventCapture): Promise<string> {
  const connected = await testEvents.waitForEvent((event) => event.kind === 'sse' && event.event === 'connected');
  return (connected as { data: { requestId: string } }).data.requestId;
}

/** Every request the page makes to the API from now on, the SSE stream's own left out. */
function apiRequests(page: Page): Request[] {
  const seen: Request[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && !path.startsWith('/api/sse/')) seen.push(request);
  });
  return seen;
}

const named = (seen: Request[]) =>
  Promise.all(
    seen.map(async (r) => ({
      call: `${r.method()} ${new URL(r.url()).pathname}`,
      connection: await r.headerValue('x-sse-request-id'),
    })),
  );

test('every request names the connection: the screens\' fetches, their writes and their timers\' polls', async ({ store, controller, triage, inbox, appBar, page, testEvents }) => {
  await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }] });
  await store.layOut(stack(1, () => ({ ruling: { verb: 'yes', note: '', at: minutesAgo(5), submitted: null } })));
  const seen = apiRequests(page);

  await triage.open();
  const requestId = await connection(testEvents);
  expect(requestId).toMatch(/^[a-z0-9]{32}$/);

  await triage.headerSubmit.click();
  await triage.finishSubmit.click();
  await expect(triage.toasts).toContainText('1 ruling submitted.');

  await appBar.inboxTab.click();
  await inbox.begin();
  // The turn is held: the screen and the Inbox pill fetch the sitting on their timers.
  const polls = () => seen.filter((r) => r.method() === 'GET' && new URL(r.url()).pathname === '/api/inbox').length;
  const before = polls();
  await expect.poll(polls, { timeout: 15_000 }).toBeGreaterThanOrEqual(before + 2);

  const calls = await named(seen);
  // The sign-in check runs before the page connects; nothing else does.
  const connected = calls.filter((c) => c.call !== 'GET /api/auth/self');
  expect(connected.filter((c) => c.connection !== requestId)).toEqual([]);
  expect(connected.map((c) => c.call)).toEqual(
    expect.arrayContaining([
      'GET /api/triage/queue',
      'GET /api/triage/actioner',
      'GET /api/inbox',
      'POST /api/triage/submit',
      'POST /api/inbox/start',
    ]),
  );
});
