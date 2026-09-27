/**
 * The triage stack (FR-23, FR-25): its order, the keys, previous, skip and next, and taking a
 * ruling back. The rules are the mockup's (FieldnotesAppSpecs/mockups/triage-ui/README.md, "Where
 * the mockup reads the plan"). Every item is invented.
 */

import { test, expect } from '../support/fixtures';
import { minutesAgo, stack } from '../support/triage-store';

test.describe('the stack', () => {
  test('returned items come first, then the oldest written; a segment per card, and a click shows its card', async ({ store, triage }) => {
    const { observations, items, ids } = stack(4, (i) =>
      i === 3 ? { question: { at: minutesAgo(10), text: 'Which runner keeps the cache?' } } : {},
    );
    await store.layOut({ observations, items });

    await triage.open();
    await triage.expectCard(ids[3]);
    await triage.expectSegments(['open', 'open', 'open', 'open']);
    await expect(triage.segments.nth(0)).toHaveClass(/here/);
    await expect(triage.card.getByTestId('triage.card.changed')).toHaveCount(0);

    await triage.segments.nth(2).click();
    await triage.expectCard(ids[1]);
    await expect(triage.segments.nth(2)).toHaveClass(/here/);
    await expect(triage.headline).toHaveText(items[1].headline);
  });

  test('an empty queue has nothing to rule on', async ({ store, triage }) => {
    await store.layOut({});

    await triage.open();
    await expect(triage.empty).toContainText('Nothing to rule on');
    await expect(triage.pane).toBeHidden();
    await expect(triage.headerSubmit).toBeHidden();
  });
});

test.describe('the keys', () => {
  test('y, n and l set the verb and take it off again; n and l go to the note, y does not', async ({ store, triage }) => {
    const { observations, items } = stack(1);
    await store.layOut({ observations, items });
    await triage.open();

    await triage.press('y');
    await triage.expectVerb('yes');
    await expect(triage.note).not.toBeFocused();
    await expect(triage.nextButton).toContainText('next');
    await triage.press('y');
    await triage.expectVerb(null);
    await expect(triage.nextButton).toContainText('skip');

    await triage.press('n');
    await triage.expectVerb('no');
    await expect(triage.note).toBeFocused();
    await expect(triage.note).toHaveAttribute('placeholder', 'Why not?');
    await triage.press('Escape');
    await expect(triage.note).not.toBeFocused();

    await triage.press('l');
    await triage.expectVerb('later');
    await expect(triage.note).toBeFocused();
    await triage.press('Escape');
    await triage.press('l');
    await triage.expectVerb(null);
  });

  test('Tab goes to the note, a key typed there is text, and Esc leaves it', async ({ store, triage }) => {
    const { observations, items } = stack(1);
    await store.layOut({ observations, items });
    await triage.open();

    await triage.press('Tab');
    await expect(triage.note).toBeFocused();
    await triage.press('y');
    await expect(triage.note).toHaveValue('y');
    await triage.expectVerb(null);
    await triage.press('Escape');
    await expect(triage.note).not.toBeFocused();
  });

  test('Ctrl+Enter rules and shows the next card, → skips, ← goes back, and ? lists the keys', async ({ store, triage }) => {
    const { observations, items, ids } = stack(3);
    await store.layOut({ observations, items });
    await triage.open();

    await triage.press('y');
    await triage.press('Control+Enter');
    await triage.expectCard(ids[1]);
    await expect.poll(() => store.ruling(ids[0])).toMatchObject({ verb: 'yes', note: '', submitted: null });
    await triage.expectSegments(['ruled', 'open', 'open']);

    await triage.press('ArrowRight');
    await triage.expectCard(ids[2]);
    await triage.expectSegments(['ruled', 'skipped', 'open']);

    await triage.press('ArrowLeft');
    await triage.expectCard(ids[1]);
    await triage.press('ArrowLeft');
    await triage.expectCard(ids[0]);
    await triage.expectVerb('yes');
    await expect(triage.previousButton).toBeDisabled();

    await triage.press('?');
    await expect(triage.keys).toBeVisible();
    await triage.press('x');
    await expect(triage.keys).toBeHidden();
    await triage.expectCard(ids[0]);
  });
});

test.describe('previous, skip and next', () => {
  test('a no or a later without a note is refused in place; with one, next rules it', async ({ store, triage }) => {
    const { observations, items, ids } = stack(2);
    await store.layOut({ observations, items });
    await triage.open();

    await triage.verb('no').click();
    await triage.nextButton.click();
    await triage.expectCard(ids[0]);
    await expect(triage.note).toHaveAttribute('placeholder', 'A no needs a note.');
    await expect(triage.note).toHaveClass(/invalid/);
    await expect(triage.note).toBeFocused();

    await triage.note.fill('Not worth a cache.');
    await expect(triage.note).not.toHaveClass(/invalid/);
    await triage.nextButton.click();
    await triage.expectCard(ids[1]);
    await expect.poll(() => store.ruling(ids[0])).toMatchObject({ verb: 'no', note: 'Not worth a cache.' });

    await triage.verb('later').click();
    await triage.press('Control+Enter');
    await triage.expectCard(ids[1]);
    await expect(triage.note).toHaveAttribute('placeholder', 'A later needs a note.');
  });

  test('← never saves: an edit left that way stays a draft, across a reload too', async ({ store, triage }) => {
    const { observations, items, ids } = stack(2);
    await store.layOut({ observations, items });
    await triage.open();

    await triage.nextButton.click(); // skip the first
    await triage.expectCard(ids[1]);
    await triage.verb('later').click();
    await triage.note.fill('When the runners move.');
    await triage.previousButton.click();
    await triage.expectCard(ids[0]);

    await triage.segments.nth(1).click();
    await triage.expectCard(ids[1]);
    await triage.expectVerb('later');
    await expect(triage.note).toHaveValue('When the runners move.');
    expect(await store.ruling(ids[1])).toBeNull();

    // The visit and the draft are the browser's: a reload comes back to both, past the cards
    // seen, which here are both.
    await triage.reload();
    await expect(triage.finish).toBeVisible();
    await triage.expectSegments(['skipped', 'open']);
    await triage.press('ArrowLeft');
    await triage.expectCard(ids[1]);
    await triage.expectVerb('later');
    await expect(triage.note).toHaveValue('When the runners move.');
  });
});

test.describe('taking a ruling back', () => {
  test('a ruled card shows its ruling; × clears the note alone; the verb taken off, → takes the ruling back and skips', async ({ store, triage }) => {
    const { observations, items, ids } = stack(2, (i) =>
      i === 0 ? { ruling: { verb: 'yes', note: 'Go ahead.', at: minutesAgo(5), submitted: null } } : {},
    );
    await store.layOut({ observations, items });
    await triage.open();

    await triage.expectCard(ids[0]);
    await triage.expectVerb('yes');
    await expect(triage.note).toHaveValue('Go ahead.');
    await triage.expectSegments(['ruled', 'open']);
    await expect(triage.headerSubmit).toBeEnabled();

    await triage.noteClear.click();
    await expect(triage.note).toHaveValue('');
    await triage.expectVerb('yes');

    await triage.press('Escape');
    await triage.press('y');
    await triage.expectVerb(null);
    await expect(triage.nextButton).toContainText('skip');
    await triage.press('ArrowRight');
    await triage.expectCard(ids[1]);
    await expect.poll(() => store.ruling(ids[0])).toBeNull();
    await triage.expectSegments(['skipped', 'open']);
    await expect(triage.headerSubmit).toBeDisabled();
  });
});
