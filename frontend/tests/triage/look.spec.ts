/**
 * The mockup's own checks (FieldnotesAppSpecs/mockups/triage-ui/README.md): text meets WCAG AA
 * in both themes, and typing in the note never scrolls the card. Every item is invented.
 */

import type { Page } from '@playwright/test';
import { test, expect } from '../support/fixtures';
import { item, minutesAgo, observation } from '../support/triage-store';

interface Failure {
  text: string;
  ratio: number;
  required: number;
  fg: string;
  bg: string;
}

/**
 * Every visible text on the page, and every icon, against the background it sits on: 4.5:1, or
 * 3:1 for large text (24px, or 18.66px bold) and icons. The background is the element's own and
 * its ancestors', composited until one is opaque; the element's and its ancestors' opacity fade
 * the text. A placeholder counts as text. Exempt: a disabled control, as WCAG exempts inactive
 * components; the meta line's `·` separators, which are decoration; and the header's help icon,
 * dimmed until hovered by the operator's call (triage.css, `.help`). Measured at rest: the mouse
 * is moved off whatever it was over.
 */
async function contrastFailures(page: Page): Promise<Failure[]> {
  await page.mouse.move(0, 0);
  return page.evaluate(() => {
    type Rgba = [number, number, number, number];
    const probe = document.createElement('canvas').getContext('2d', { willReadFrequently: true })!;
    // The browser's own parser: any colour syntax, painted and read back as sRGB bytes.
    const rgba = (color: string): Rgba => {
      probe.clearRect(0, 0, 1, 1);
      probe.fillStyle = '#000';
      probe.fillStyle = color;
      probe.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a / 255];
    };
    const over = (top: Rgba, below: Rgba): Rgba => {
      const a = top[3] + below[3] * (1 - top[3]);
      if (a === 0) return [0, 0, 0, 0];
      const mix = (i: number) => (top[i] * top[3] + below[i] * below[3] * (1 - top[3])) / a;
      return [mix(0), mix(1), mix(2), a];
    };
    const luminance = ([r, g, b]: Rgba) => {
      const lin = (c: number) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    };
    const background = (el: Element): Rgba => {
      const layers: Rgba[] = [];
      for (let node: Element | null = el; node; node = node.parentElement) {
        const layer = rgba(getComputedStyle(node).backgroundColor);
        if (layer[3] > 0) layers.push(layer);
        if (layer[3] >= 1) break;
      }
      return layers.reduceRight<Rgba>((below, top) => over(top, below), [255, 255, 255, 1]);
    };
    const opacity = (el: Element) => {
      let value = 1;
      for (let node: Element | null = el; node; node = node.parentElement) {
        value *= Number(getComputedStyle(node).opacity);
      }
      return value;
    };
    const show = ([r, g, b, a]: Rgba) => `rgba(${r.toFixed(0)}, ${g.toFixed(0)}, ${b.toFixed(0)}, ${a.toFixed(2)})`;

    const failures: Failure[] = [];
    const check = (el: Element, text: string, required: number, css = getComputedStyle(el).color) => {
      const bg = background(el);
      const color = rgba(css);
      const fg = over([color[0], color[1], color[2], color[3] * opacity(el)], bg);
      const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
      const ratio = (hi + 0.05) / (lo + 0.05);
      if (ratio < required) {
        failures.push({ text: text.slice(0, 60), ratio: Math.round(ratio * 100) / 100, required, fg: show(fg), bg: show(bg) });
      }
    };
    const shown = (el: Element) => {
      const box = el.getBoundingClientRect();
      return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) && box.width > 1 && box.height > 1;
    };

    for (const el of Array.from(document.body.querySelectorAll('*'))) {
      if (!shown(el) || el.closest(':disabled, [aria-hidden="true"]:not(svg)')) continue;
      if (el instanceof SVGSVGElement) {
        if (el.closest('.help')) continue;
        check(el, `icon in ${el.parentElement?.className || el.parentElement?.tagName}`, 3);
        continue;
      }
      if (el.closest('svg')) continue;
      const own = Array.from(el.childNodes)
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent ?? '')
        .join('')
        .trim();
      if (el instanceof HTMLTextAreaElement && el.placeholder && !el.value) {
        check(el, `placeholder: ${el.placeholder}`, 4.5, getComputedStyle(el, '::placeholder').color);
      }
      if (!own || /^[·\s]+$/.test(own)) continue;
      const style = getComputedStyle(el);
      const size = parseFloat(style.fontSize);
      const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
      check(el, own, large ? 3 : 4.5);
    }
    return failures;
  });
}

test.describe('the mockup checks', () => {
  for (const theme of ['light', 'dark'] as const) {
    test(`text meets WCAG AA in the ${theme} theme`, async ({ store, triage, page }) => {
      // A card with everything on it: a question, a changed observation, reports old and new,
      // links, issue keys and code; then one of each category, one of them ruled.
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
        ruling: { verb: 'no', note: 'Not now.', at: minutesAgo(60), submitted: null },
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
