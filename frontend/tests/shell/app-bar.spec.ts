/**
 * The app bar (FR-33): the screen tabs, and the two pills on both screens, each shown only while
 * it has something to show, its tooltip saying what. The Actioner pill opens the actioner page
 * while the actioner is busy, and the failure's dialog once it is idle with one. The worker
 * backend's fake controller plays the inbox sitting's session and the actioner's runs. Every item
 * and sitting is invented.
 */

import type { APIRequestContext } from '@playwright/test';
import { contrastFailures, type Failure } from '../support/contrast';
import { test, expect } from '../support/fixtures';
import type { TriagePage } from '../support/page-objects/triage-page';
import { minutesAgo, stack } from '../support/triage-store';

/** An editor's write to the API, as the screens make it: Start, a turn, a submit. */
async function write(request: APIRequestContext, url: string, data: object = {}): Promise<void> {
  const response = await request.post(url, { data });
  expect(response.ok(), await response.text()).toBe(true);
}

const RULING = { verb: 'yes' as const, note: '', at: minutesAgo(5), submitted: null };

/** One item, ruled and not yet submitted: what a submit sends. */
const ruled = () => stack(1, () => ({ ruling: RULING }));

/** Two items, the first ruled: after a submit, the second is still to rule on. */
const ruledAndOpen = () => stack(2, (i) => ({ ruling: i === 0 ? RULING : null }));

/** The actioner's state as the API answers it now. */
async function actionerState(request: APIRequestContext, backendUrl: string): Promise<string> {
  const response = await request.get(`${backendUrl}/api/triage/actioner`);
  expect(response.ok(), await response.text()).toBe(true);
  return ((await response.json()) as { state: string }).state;
}

/** The finish card's Submit, from the ruled card the page opens on. */
async function submit(triage: TriagePage): Promise<void> {
  await triage.headerSubmit.click();
  await triage.finishSubmit.click();
  await expect(triage.toasts).toContainText('1 ruling submitted.');
}

test.describe('the screen tabs', () => {
  test('lead from one screen to the other, the one shown marked', async ({ store, controller, triage, inbox, appBar, page }) => {
    await store.layOut({});
    await controller.layOut();
    await triage.open();
    await appBar.expectScreen('triage');

    await appBar.inboxTab.click();
    await expect(inbox.header).toBeVisible();
    await expect(page).toHaveURL(/\/inbox$/);
    await appBar.expectScreen('inbox');

    await appBar.triageTab.click();
    await expect(triage.empty).toBeVisible();
    await appBar.expectScreen('triage');

    await inbox.open();
    await appBar.expectScreen('inbox');
  });
});

test.describe('the Inbox pill', () => {
  test('nothing without a sitting; breathing while a turn is in flight, steady while the session waits, amber once lost; a link to the inbox screen', async ({ store, controller, triage, inbox, appBar, page, request, backendUrl }) => {
    await store.layOut({});
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }] });
    await appBar.fetched(() => triage.open());
    await expect(appBar.inboxPill).toHaveCount(0);
    await expect(appBar.actionerPill).toHaveCount(0);

    // Start: the session takes its first turn, held until the controller finishes it.
    await write(request, `${backendUrl}/api/inbox/start`);
    await triage.reload();
    await appBar.expectPill(appBar.inboxPill, 'working', /^The inbox session is working$/);

    // Fetched again on its timer while the turn is in flight.
    await controller.finish('The inbox, sorted, and two questions.');
    await appBar.expectPill(appBar.inboxPill, 'waiting', /^The inbox session waits for your answer$/);

    await appBar.inboxPill.click();
    await expect(inbox.header).toBeVisible();
    await expect(page).toHaveURL(/\/inbox$/);
    await appBar.expectPill(appBar.inboxPill, 'waiting', /waits for your answer/);

    await write(request, `${backendUrl}/api/inbox/turns`, { message: 'Yes to both.' });
    await inbox.open();
    await appBar.expectPill(appBar.inboxPill, 'working', /is working/);

    // Lost mid-turn: the API finds it on its next call to the controller.
    await controller.lose('session');
    await appBar.expectPill(appBar.inboxPill, 'lost', /^The inbox sitting was lost at \d{4}-\d\d-\d\d \d\d:\d\d: .+/);

    await appBar.triageTab.click();
    await expect(triage.empty).toBeVisible();
    await appBar.expectPill(appBar.inboxPill, 'lost', /was lost/);
  });
});

test.describe('the Inbox pill, once the idle expiry has ended the sitting', () => {
  test('is amber on both screens, its tooltip saying when it ended and after how long idle', async ({ store, controller, triage, inbox, appBar, request, backendUrl }) => {
    await store.layOut({});
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }] });
    await write(request, `${backendUrl}/api/inbox/start`);
    await controller.finish('The inbox, sorted.');
    await expect
      .poll(async () => ((await (await request.get(`${backendUrl}/api/inbox`)).json()) as { sitting: { turn: unknown } }).sitting.turn)
      .toBeNull();
    await controller.expire();

    await triage.open();
    await appBar.expectPill(
      appBar.inboxPill,
      'ended',
      /^The inbox sitting ended at \d{4}-\d\d-\d\d \d\d:\d\d after 30 minutes idle$/,
    );
    await appBar.inboxPill.click();
    await expect(inbox.ended).toBeVisible();
    await appBar.expectPill(appBar.inboxPill, 'ended', /ended at .+ after 30 minutes idle$/);
  });
});

test.describe('the Actioner pill', () => {
  test('nothing while idle; breathing from a submit until the run ends; red after a failed run, on both screens, until the next start', async ({ store, controller, triage, inbox, appBar }) => {
    await controller.layOut();
    const first = ruled();
    await store.layOut(first);
    await appBar.fetched(() => triage.open());
    await expect(appBar.actionerPill).toHaveCount(0);

    // Fetched at once on the submit: idle, its timer is longer than the wait.
    await submit(triage);
    await appBar.expectPill(appBar.actionerPill, 'working', /^The actioner is (starting|running, since .+)$/);
    await appBar.expectPill(appBar.actionerPill, 'working', /^The actioner is running, since \d{4}-\d\d-\d\d \d\d:\d\d$/);

    await controller.runEnd('failed', 'the session ended in an error');
    await appBar.expectPill(
      appBar.actionerPill,
      'failed',
      /^The actioner's last run failed at \d{4}-\d\d-\d\d \d\d:\d\d: the session ended in an error$/,
    );

    await appBar.inboxTab.click();
    await expect(inbox.header).toBeVisible();
    await appBar.expectPill(appBar.actionerPill, 'failed', /last run failed/);

    // The next start takes the red away.
    await store.layOut(ruled());
    await triage.open();
    await expect(appBar.actionerPill).toHaveAttribute('data-state', 'failed');
    await submit(triage);
    await appBar.expectPill(appBar.actionerPill, 'working', /^The actioner is running/);
  });

  test('a skipped run waiting to be tried again breathes, its tooltip saying why, and opens the actioner page over the stack', async ({ store, controller, triage, appBar }) => {
    await controller.layOut();
    const { observations, items, ids } = ruledAndOpen();
    await store.layOut({ observations, items });
    await triage.open();
    await submit(triage);
    await appBar.expectPill(appBar.actionerPill, 'working', /^The actioner is running/);

    await controller.runEnd('skipped', 'in-use');
    await appBar.expectPill(
      appBar.actionerPill,
      'working',
      /^The actioner's last run was skipped at \d{4}-\d\d-\d\d \d\d:\d\d: in-use; it is tried again$/,
    );
    await triage.expectCard(ids[1]);
    await appBar.actionerPill.click();
    await expect(triage.actionerTitle).toHaveText('The actioner is starting');
    await expect(triage.card).toHaveCount(0);
  });

  test('a start the controller refuses is red, and its dialog says so', async ({ store, controller, triage, appBar }) => {
    await controller.layOut({ runs: [503] });
    await store.layOut(ruled());
    await triage.open();
    await submit(triage);
    await appBar.expectPill(appBar.actionerPill, 'failed', /^The controller refused the actioner's start at \d{4}-\d\d-\d\d \d\d:\d\d: .+/);

    await appBar.actionerPill.click();
    await expect(appBar.failureWhat).toHaveText("The controller refused the actioner's start");
    await expect(appBar.failureWhen).toHaveText(/^\d{4}-\d\d-\d\d \d\d:\d\d$/);
    await expect(appBar.failureReason).not.toBeEmpty();
  });
});

test.describe('the Actioner pill, while the actioner is busy', () => {
  test('opens the actioner page over items still to rule on, from either screen; the Triage tab leaves it for the stack; the stack again once the run has ended', async ({ store, controller, triage, inbox, appBar, page }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], run_turns: [{ lines: ['Reading the rulings'], held: true }] });
    const { observations, items, ids } = ruledAndOpen();
    await store.layOut({ observations, items });
    await triage.open();
    await submit(triage);
    await triage.expectCard(ids[1]);
    await appBar.expectPill(appBar.actionerPill, 'working', /^The actioner is running/);
    await expect(triage.actioner).toHaveCount(0);

    await appBar.actionerPill.click();
    await expect(triage.actionerLines).toHaveText(['Reading the rulings']);
    await expect(triage.card).toHaveCount(0);
    await expect(triage.pane).toBeHidden();
    await expect(triage.headerSubmit).toBeHidden();
    await expect(page).toHaveURL(/\/\?actioner=true$/);
    await appBar.expectScreen('triage');
    // The triage keys rule no card from it.
    await triage.press('y');
    await triage.press('ArrowLeft');
    await expect(triage.actioner).toBeVisible();

    await appBar.triageTab.click();
    await triage.expectCard(ids[1]);
    await triage.expectVerb(null);
    await expect(triage.actioner).toHaveCount(0);

    await appBar.inboxTab.click();
    await expect(inbox.header).toBeVisible();
    await appBar.actionerPill.click();
    await expect(triage.actionerLines).toHaveText(['Reading the rulings']);
    await appBar.expectScreen('triage');

    // A card's segment leaves the page for that card.
    await triage.segments.first().click();
    await triage.expectCard(ids[1]);
    await expect(page).toHaveURL(/\/$/);

    await appBar.actionerPill.click();
    await expect(triage.actioner).toBeVisible();
    await controller.runEnd('failed', 'the session ended in an error');
    await triage.expectCard(ids[1]);
    await expect(triage.actioner).toHaveCount(0);
    await expect(page).toHaveURL(/\/$/);
    await expect(appBar.actionerPill).toHaveAttribute('data-state', 'failed');
  });
});

test.describe('the failed Actioner pill', () => {
  test("opens the failure's dialog: what, when and why; Cancel, Escape and a click beside it leave the failure and the pill; Close clears both, from either screen", async ({ store, controller, triage, inbox, appBar, page }) => {
    await controller.layOut();
    const { observations, items, ids } = ruledAndOpen();
    await store.layOut({ observations, items });
    await triage.open();
    await submit(triage);
    await appBar.expectPill(appBar.actionerPill, 'working', /^The actioner is running/);
    await controller.runEnd('failed', 'the session ended in an error');
    await appBar.expectPill(appBar.actionerPill, 'failed', /last run failed/);

    await appBar.actionerPill.click();
    await expect(appBar.failureWhat).toHaveText("The actioner's last run failed");
    await expect(appBar.failureWhen).toHaveText(/^\d{4}-\d\d-\d\d \d\d:\d\d$/);
    await expect(appBar.failureReason).toHaveText('the session ended in an error');
    // The triage keys do not reach the card beneath.
    await triage.press('y');
    await appBar.failureCancel.click();
    await expect(appBar.failure).toHaveCount(0);
    await triage.expectCard(ids[1]);
    await triage.expectVerb(null);
    await appBar.expectPill(appBar.actionerPill, 'failed', /last run failed/);

    await appBar.actionerPill.click();
    await expect(appBar.failure).toBeVisible();
    await triage.press('Escape');
    await expect(appBar.failure).toHaveCount(0);
    await appBar.actionerPill.click();
    await expect(appBar.failure).toBeVisible();
    await page.mouse.click(5, 700);
    await expect(appBar.failure).toHaveCount(0);
    await triage.reload();
    await expect(appBar.actionerPill).toHaveAttribute('data-state', 'failed');

    await appBar.inboxTab.click();
    await expect(inbox.header).toBeVisible();
    await appBar.actionerPill.click();
    await appBar.failureClose.click();
    await expect(appBar.failure).toHaveCount(0);
    await expect(appBar.actionerPill).toHaveCount(0);
    await appBar.fetched(() => inbox.open());
    await expect(appBar.actionerPill).toHaveCount(0);
  });

  test('Close clears only the failure it showed: one recorded meanwhile stays, and the pill shows it', async ({ store, controller, triage, appBar, request, backendUrl }) => {
    // The dialog stays open past the pill's next fetch, 20 s apart while the actioner is idle.
    test.slow();
    await controller.layOut();
    await store.layOut(ruled());
    await triage.open();
    await submit(triage);
    await appBar.expectPill(appBar.actionerPill, 'working', /^The actioner is running/);
    await controller.runEnd('failed', 'the first run failed');
    await appBar.expectPill(appBar.actionerPill, 'failed', /: the first run failed$/);
    await appBar.actionerPill.click();
    await expect(appBar.failureReason).toHaveText('the first run failed');

    // Meanwhile another submit starts a run, which fails.
    await store.layOut(ruled());
    await write(request, `${backendUrl}/api/triage/submit`);
    await expect.poll(() => actionerState(request, backendUrl)).toBe('running');
    await controller.runEnd('failed', 'the second run failed');

    // The pill's fetch brings the newer failure in beneath the open dialog, which keeps its own.
    await expect(appBar.actionerPill).toHaveAttribute('title', /: the second run failed$/, { timeout: 25_000 });
    await expect(appBar.failureReason).toHaveText('the first run failed');
    await appBar.failureClose.click();
    await expect(appBar.failure).toHaveCount(0);
    await appBar.expectPill(appBar.actionerPill, 'failed', /^The actioner's last run failed at .+: the second run failed$/);
  });
});

test("the app bar meets WCAG AA in both themes, with every look of the pills and the failure's dialog", async ({ store, controller, triage, appBar, page, request, backendUrl }) => {
  await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }], runs: [503] });
  await store.layOut(ruled());
  await write(request, `${backendUrl}/api/inbox/start`);
  await write(request, `${backendUrl}/api/triage/submit`);
  await triage.open();
  await expect(triage.empty).toBeVisible();
  await expect(appBar.inboxPill).toHaveAttribute('data-state', 'working');
  await expect(appBar.actionerPill).toHaveAttribute('data-state', 'failed');

  const views: Record<string, Failure[]> = {};
  for (const theme of ['light', 'dark'] as const) {
    await triage.setTheme(theme);
    views[`working and failed, ${theme}`] = await contrastFailures(page);
    await appBar.actionerPill.click();
    await expect(appBar.failure).toBeVisible();
    views[`the failure's dialog, ${theme}`] = await contrastFailures(page);
    await appBar.failureCancel.click();
  }
  await controller.lose('session');
  await expect(appBar.inboxPill).toHaveAttribute('data-state', 'lost');
  views['lost, dark'] = await contrastFailures(page);

  expect(Object.fromEntries(Object.entries(views).filter(([, failures]) => failures.length))).toEqual({});
});
