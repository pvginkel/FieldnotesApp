/**
 * A round of triage (FR-23 to FR-26): the finish card and Start over, a submit, an item the
 * actioner returned with a question, an observation that changed since its item was written, and
 * a write the store refuses. Every item is invented.
 */

import { test, expect } from '../support/fixtures';
import { item, minutesAgo, observation, stack } from '../support/triage-store';

test.describe('the finish card', () => {
  test('with nothing ruled, Start over goes back to the first card with the skips forgotten', async ({ store, triage }) => {
    const { observations, items, ids } = stack(2);
    await store.layOut({ observations, items });
    await triage.open();

    await triage.press('ArrowRight');
    await triage.press('ArrowRight');
    await expect(triage.finish).toContainText('Nothing ruled yet');
    await expect(triage.finishSubmit).toHaveCount(0);
    await expect(triage.headerSubmit).toBeHidden();
    await expect(triage.pane).toBeHidden();
    await triage.expectSegments(['skipped', 'skipped']);

    await triage.finishPrevious.click();
    await triage.expectCard(ids[1]);
    await triage.press('ArrowRight');
    await expect(triage.finish).toBeVisible();

    await triage.finishRestart.click();
    await triage.expectCard(ids[0]);
    await triage.expectSegments(['open', 'open']);
    await triage.reload();
    await triage.expectCard(ids[0]);
  });

  test('a submit sends the rulings, says so, and starts a new round at the first card left', async ({ store, triage }) => {
    const { observations, items, ids } = stack(3);
    await store.layOut({ observations, items });
    await triage.open();
    await expect(triage.headerSubmit).toBeDisabled();

    await triage.press('y');
    await triage.press('Control+Enter');
    await triage.press('n');
    await triage.note.pressSequentially('Out of scope.');
    await triage.press('Control+Enter');
    await triage.press('ArrowRight');

    await expect(triage.finish).toContainText('Ready to submit');
    // Ctrl+Enter does not submit from the finish card: Submit is a click.
    await triage.press('Control+Enter');
    await expect(triage.finishSubmit).toHaveText('Submit 2');
    await triage.finishSubmit.click();

    await expect(triage.toasts).toContainText('2 rulings submitted.');
    await triage.expectCard(ids[2]);
    await triage.expectSegments(['open']);
    for (const [id, verb] of [[ids[0], 'yes'], [ids[1], 'no']]) {
      const ruling = await store.ruling(id);
      expect(ruling).toMatchObject({ verb });
      expect(ruling?.submitted).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
    }
    expect(await store.ruling(ids[2])).toBeNull();
  });

  test("the header's Submit goes to the finish card from a card, and sends nothing", async ({ store, triage }) => {
    const { observations, items, ids } = stack(2);
    await store.layOut({ observations, items });
    await triage.open();

    await triage.press('y');
    await triage.press('Control+Enter');
    await triage.expectCard(ids[1]);
    await triage.headerSubmit.click();

    await expect(triage.finish).toContainText('Ready to submit');
    await expect(triage.finishSubmit).toHaveText('Submit 1');
    await expect(triage.headerSubmit).toBeHidden();
    await expect.poll(() => store.ruling(ids[0])).toMatchObject({ verb: 'yes', submitted: null });

    await triage.finishSubmit.click();
    await expect(triage.toasts).toContainText('1 ruling submitted.');
    await triage.expectCard(ids[1]);
    await expect.poll(async () => (await store.ruling(ids[0]))?.submitted).toBeTruthy();
  });
});

test.describe('a returned item', () => {
  test('comes first, shows the question and the old ruling, and is unruled until ruled again', async ({ store, triage }) => {
    const question = { at: minutesAgo(30), text: 'Which runner should keep the cache?' };
    const { observations, items, ids } = stack(2, (i) =>
      i === 1
        ? { question, ruling: { verb: 'no', note: 'Not now.', at: minutesAgo(120), submitted: null } }
        : {},
    );
    await store.layOut({ observations, items });
    await triage.open();

    await triage.expectCard(ids[1]);
    const box = triage.card.getByTestId('triage.card.question');
    await expect(box).toContainText('The actioner asks');
    await expect(box).toContainText(question.text);
    await expect(box).toContainText('Your ruling was no: Not now.');
    // The pane starts empty: the old ruling is in the actioner's box.
    await triage.expectVerb(null);
    await expect(triage.note).toHaveValue('');
    await triage.expectSegments(['open', 'open']);
    await expect(triage.headerSubmit).toBeDisabled();

    await triage.press('y');
    await triage.press('Tab');
    await triage.note.pressSequentially('The shared one.');
    await triage.press('Control+Enter');
    await triage.expectCard(ids[0]);
    await expect.poll(() => store.ruling(ids[1])).toMatchObject({ verb: 'yes', note: 'The shared one.' });
    expect((await store.ruling(ids[1]))!.at > question.at).toBe(true);
    await triage.expectSegments(['ruled', 'open']);
    await expect(triage.headerSubmit).toBeEnabled();
  });
});

test.describe('an observation changed since', () => {
  test('the card flags what changed, and the store section says it too', async ({ store, triage }) => {
    const was = observation({ canonical: 'Widget builds start cold.' });
    const it = item(was);
    const now = observation({
      ...was,
      status: 'raised',
      card: 'FN-1',
      canonical: 'Widget builds start cold on every runner.',
      last_seen: minutesAgo(5),
      reactions: [...was.reactions, { at: minutesAgo(5), emoji: '👍', repo: 'acme/gadgets', session: null, client: 'mcp', text: null }],
    });
    // A long card, so the store section starts out of view.
    await store.layOut({ observations: [now], items: [{ ...it, evidence: Array(40).fill('A line of evidence.').join('\n\n') }] });
    await triage.open();

    const chip = triage.card.getByTestId('triage.card.changed');
    await expect(chip).toContainText('carded FN-1');
    await expect(chip).toContainText('open → raised');
    await expect(chip).toContainText('reported again');
    await expect(chip).toContainText('statement rewritten');

    const section = triage.card.getByTestId('triage.card.store');
    await expect(section.locator('.changes')).toContainText('Changed since: carded FN-1');
    await expect(section).toContainText('Widget builds start cold on every runner.');
    await expect(section.getByRole('link', { name: 'FN-1' })).toHaveAttribute('href', /\/issue\/FN-1$/);

    await expect(section).not.toBeInViewport();
    await chip.click();
    await expect(section).toBeInViewport();
  });

  test('an observation gone from the store is flagged as merged away or retired', async ({ store, triage }) => {
    const it = item(observation());
    await store.layOut({ items: [it] });
    await triage.open();

    await expect(triage.card.getByTestId('triage.card.changed')).toContainText(
      'merged away or retired since the item was written',
    );
    await expect(triage.card).toContainText('The observation is no longer in the store');
  });
});

test.describe('a refused write', () => {
  test('an item rewritten after the page loaded refuses the ruling: a toast, the card as the store has it, the note back as a draft', async ({ store, triage }) => {
    const { observations, items, ids } = stack(2);
    await store.layOut({ observations, items });
    await triage.open();

    // The reconciler rewrites the first item while the page is open.
    const rewritten = { ...items[0], written: minutesAgo(1), headline: 'Widget builds start cold, rewritten' };
    await store.layOut({ observations, items: [rewritten, items[1]] });

    await triage.press('l');
    await triage.note.pressSequentially('After the runner move.');
    await triage.press('Control+Enter');
    await triage.expectCard(ids[1]);
    await expect(triage.toasts).toContainText('was rewritten after the page loaded');

    await triage.press('ArrowLeft');
    await triage.expectCard(ids[0]);
    await expect(triage.headline).toHaveText(rewritten.headline);
    await triage.expectVerb('later');
    await expect(triage.note).toHaveValue('After the runner move.');
    expect(await store.ruling(ids[0])).toBeNull();

    // Ruled again on the item as it now stands, it lands.
    await triage.press('Control+Enter');
    await triage.expectCard(ids[1]);
    await expect.poll(() => store.ruling(ids[0])).toMatchObject({ verb: 'later', note: 'After the runner move.' });
  });
});
