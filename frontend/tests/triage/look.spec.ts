/**
 * The mockup's own checks (FieldnotesAppSpecs/mockups/triage-ui/README.md): text meets WCAG AA
 * in both themes, and typing in the note never scrolls the card. Every item is invented.
 */

import { contrastFailures, type Failure } from '../support/contrast';
import { test, expect } from '../support/fixtures';
import { item, minutesAgo, observation } from '../support/triage-store';

test.describe('the mockup checks', () => {
  for (const theme of ['light', 'dark'] as const) {
    test(`text meets WCAG AA in the ${theme} theme`, async ({ store, triage, page }) => {
      // A card with everything on it: a question, a changed observation, the reconciler's
      // proposal and the old ruling that applied it, reports old and new, links, issue keys and
      // code; then one of each category, one of them ruled.
      const was = observation({ category: 'friction' });
      const now = observation({
        ...was,
        status: 'raised',
        card: 'FN-1',
        reason: 'Cache on the shared runner, see FN-2.',
        last_seen: minutesAgo(5),
        reactions: [...was.reactions, { at: minutesAgo(5), emoji: '👍', repo: 'acme/gadgets', session: 's-2', client: 'mcp', text: 'Seen on acme/gadgets too.' }],
      });
      const returned = item(was, {
        written: minutesAgo(90),
        question: { at: minutesAgo(30), text: 'Should **acme/widgets** keep `~/.cache`? See https://example.invalid/runners.' },
        proposal: { verb: 'no', note: 'Not now.' },
        ruling: { verb: 'no', note: 'Not now.', at: minutesAgo(60), submitted: null, guessed: true },
        reports: [...was.reactions.map((r) => ({ ...r, new: false })), { ...now.reactions[1], new: true }],
      });
      const hint = observation({ category: 'hint' });
      const idea = observation({ category: 'idea' });
      await store.layOut({
        observations: [now, hint, idea],
        items: [
          returned,
          item(hint, { written: minutesAgo(80), ruling: { verb: 'yes', note: 'Go ahead.', at: minutesAgo(10), submitted: null } }),
          item(idea, { written: minutesAgo(70) }),
        ],
      });

      await triage.open();
      await triage.setTheme(theme);
      const views: Record<string, Failure[]> = {};

      views['returned card'] = await contrastFailures(page);
      await triage.press('n');
      await triage.nextButton.click(); // refused: the note's placeholder turns red
      views['refused note'] = await contrastFailures(page);
      await triage.press('Escape');
      await triage.press('n');
      await triage.press('ArrowRight');
      views['ruled card'] = await contrastFailures(page);
      await triage.press('ArrowRight');
      views['idea card'] = await contrastFailures(page);
      await triage.press('?');
      views['keys'] = await contrastFailures(page);
      await triage.press('Escape');
      await triage.press('ArrowRight');
      await expect(triage.finish).toBeVisible();
      views['finish card'] = await contrastFailures(page);
      await page.getByTestId('app-shell.topbar.user').click();
      views['user menu'] = await contrastFailures(page);

      expect(Object.fromEntries(Object.entries(views).filter(([, failures]) => failures.length))).toEqual({});
    });
  }

  test('typing in the note does not scroll the card', async ({ store, triage }) => {
    const it = item(observation(), { evidence: Array(60).fill('A line of evidence, long enough to scroll.').join('\n\n') });
    await store.layOut({ observations: [], items: [it] });
    await triage.open();

    await triage.main.evaluate((main) => (main.scrollTop = 600));
    const scrolled = await triage.main.evaluate((main) => main.scrollTop);
    expect(scrolled).toBeGreaterThan(0);

    await triage.press('n');
    await expect(triage.note).toBeFocused();
    const before = await triage.note.evaluate((note) => note.getBoundingClientRect().height);
    await triage.note.pressSequentially('Not while the runners are shared, and not before the cache has an owner who can ');
    await triage.press('Enter');
    await triage.note.pressSequentially('size it. A second line.');
    await triage.press('Enter');
    await triage.note.pressSequentially('And a third.');

    // The note grew with what was typed, and the card stayed where it was.
    expect(await triage.note.evaluate((note) => note.getBoundingClientRect().height)).toBeGreaterThan(before);
    expect(await triage.main.evaluate((main) => main.scrollTop)).toBe(scrolled);
  });
});
