/**
 * The actioner page (FR-33): while a start is pending or a run is held, the triage screen shows
 * the run's last log lines in the inbox's working box, in place of "Nothing to rule on", and once
 * the run has ended, what it would otherwise show. The worker backend's fake controller plays the
 * run's session, its lines and its end (FR-27). Every item and line is invented.
 */

import { contrastFailures } from '../support/contrast';
import { test, expect } from '../support/fixtures';
import type { TriagePage } from '../support/page-objects/triage-page';
import { minutesAgo, stack } from '../support/triage-store';

/** One item, ruled and not yet submitted: what a submit sends. */
const ruled = () => stack(1, () => ({ ruling: { verb: 'yes', note: '', at: minutesAgo(5), submitted: null } }));

/** The finish card's Submit, from the ruled card the page opens on. */
async function submit(triage: TriagePage): Promise<void> {
  await triage.headerSubmit.click();
  await triage.finishSubmit.click();
  await expect(triage.toasts).toContainText('1 ruling submitted.');
}

test.describe('the actioner page', () => {
  test('takes the place of "Nothing to rule on" from the submit: the waiting text, then the run\'s last four lines, the spinner beside the last, each line cut on its own; a reload starts with them; "Nothing to rule on" once the run has succeeded', async ({ store, controller, triage, appBar, page }) => {
    await page.setViewportSize({ width: 640, height: 800 });
    await controller.layOut({ environments: [{ env_id: 'env-1' }], run_turns: [{ held: true }] });
    await store.layOut(ruled());
    await triage.open();
    await submit(triage);

    await expect(triage.actioner).toBeVisible();
    await expect(triage.empty).toHaveCount(0);
    await expect(triage.actionerWaiting).toHaveText('Waiting for the run…');
    await expect(triage.actionerLines).toHaveCount(0);
    await expect(triage.actionerSpinner).toBeVisible();
    await expect(triage.pane).toBeHidden();
    await expect(triage.headerSubmit).toBeHidden();

    const lines = [
      'Reading the rulings',
      'Closing EX-1',
      'Asking the question on EX-2',
      'Pushing the store',
      // 80 characters, as the controller cuts a line: wider than the box at this width.
      `Bash: git -C /work/scratch/example log --oneline --since=yesterday -- docs/ src/…`,
    ];
    for (const [i, line] of lines.entries()) {
      await controller.runEmit(line);
      const shown = lines.slice(Math.max(0, i - 3), i + 1);
      await expect(triage.actionerLines).toHaveText(shown);
      await expect(triage.actionerWaiting).toHaveCount(0);
      // Nothing but the lines and the spinner.
      await expect(triage.actionerWorking).toHaveText(shown.join(''));
      await expect(triage.actionerWorking.locator('svg')).toHaveCount(1);
      // The spinner beside the last line.
      const spinner = (await triage.actionerSpinner.boundingBox())!;
      const last = (await triage.actionerLines.last().boundingBox())!;
      const middle = spinner.y + spinner.height / 2;
      expect(middle).toBeGreaterThan(last.y);
      expect(middle).toBeLessThan(last.y + last.height);
    }
    await expect(triage.actionerTitle).toHaveText('The actioner is running');

    // Each line one line high, the long one cut with an ellipsis of its own, the others whole.
    const cut = await triage.actionerLines.evaluateAll((items) =>
      items.map((item) => {
        const style = getComputedStyle(item);
        return {
          oneLine: Math.abs(item.getBoundingClientRect().height - parseFloat(style.lineHeight)) < 1,
          ellipsis: style.textOverflow === 'ellipsis' && style.whiteSpace === 'nowrap',
          cut: item.scrollWidth > item.clientWidth,
        };
      }),
    );
    expect(cut).toEqual([
      { oneLine: true, ellipsis: true, cut: false },
      { oneLine: true, ellipsis: true, cut: false },
      { oneLine: true, ellipsis: true, cut: false },
      { oneLine: true, ellipsis: true, cut: true },
    ]);

    // A page opened mid-run starts with the lines the API holds.
    await triage.reload();
    await expect(triage.actionerLines).toHaveText(lines.slice(-4));

    // The run carried the item out, so nothing waits once its webhook reports the success.
    await controller.runFinish('Both rulings carried out.');
    await store.layOut({});
    await controller.runEnd(null);
    await expect(triage.empty).toContainText('Nothing to rule on');
    await expect(triage.actioner).toHaveCount(0);
    await expect(appBar.actionerPill).toHaveCount(0);
  });

  test('a run too short to show a line keeps the waiting text until its end; a failed run gives way to "Nothing to rule on", the pill red', async ({ store, controller, triage, appBar }) => {
    // No turn left for the run: its session is over at once, with no line.
    await controller.layOut({ environments: [{ env_id: 'env-1' }] });
    await store.layOut(ruled());
    await triage.open();
    await submit(triage);

    await expect(triage.actionerTitle).toHaveText('The actioner is running');
    await expect(triage.actionerWaiting).toHaveText('Waiting for the run…');
    await expect.poll(async () => (await controller.read()).runs).toHaveLength(1);
    await triage.reload();
    await expect(triage.actionerWaiting).toHaveText('Waiting for the run…');
    await expect(triage.actionerLines).toHaveCount(0);

    await controller.runEnd('failed', 'the session ended in an error');
    await expect(triage.empty).toContainText('Nothing to rule on');
    await expect(triage.actioner).toHaveCount(0);
    await appBar.expectPill(appBar.actionerPill, 'failed', /last run failed .*: the session ended in an error$/);
  });

  test('a skipped run waiting to be tried again shows the page starting, with the waiting text', async ({ store, controller, triage }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], run_turns: [{ lines: ['Reading the rulings'], held: true }] });
    await store.layOut(ruled());
    await triage.open();
    await submit(triage);
    await expect(triage.actionerLines).toHaveText(['Reading the rulings']);

    await controller.runEnd('skipped', 'in-use');
    await expect(triage.actionerTitle).toHaveText('The actioner is starting');
    await expect(triage.actionerWaiting).toHaveText('Waiting for the run…');
    await expect(triage.actionerLines).toHaveCount(0);
  });

  test('meets WCAG AA in both themes', async ({ store, controller, triage, page }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], run_turns: [{ lines: ['Reading the rulings'], held: true }] });
    await store.layOut(ruled());
    await triage.open();
    await submit(triage);
    await expect(triage.actionerLines).toHaveText(['Reading the rulings']);

    const views: Record<string, Awaited<ReturnType<typeof contrastFailures>>> = {};
    for (const theme of ['light', 'dark'] as const) {
      await triage.setTheme(theme);
      views[theme] = await contrastFailures(page);
    }
    expect(Object.fromEntries(Object.entries(views).filter(([, failures]) => failures.length))).toEqual({});
  });
});
