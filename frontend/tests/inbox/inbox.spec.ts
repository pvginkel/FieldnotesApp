/**
 * The inbox screen (FR-28 to FR-32): Start, the transcript with the working box under it, a reply
 * as its turn's messages, the composer, a lost sitting, one the idle time rested, and End. The
 * worker backend's fake controller plays the sitting's session. Every sitting, card id and title
 * is invented.
 */

import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { contrastFailures, type Failure } from '../support/contrast';
import { test, expect } from '../support/fixtures';
import type { TestEventCapture } from '../support/helpers/test-events';

/** The sitting as `GET /api/inbox` answers it. */
async function inboxState(request: APIRequestContext, backendUrl: string) {
  const response = await request.get(`${backendUrl}/api/inbox`);
  expect(response.ok(), await response.text()).toBe(true);
  return (await response.json()) as {
    tracker: string | null;
    sitting: { turn: { task: string; lines: string[] } | null } | null;
  };
}

/** The page's next read of the sitting whose turn holds exactly `lines`. */
function readHolding(page: Page, lines: string[]) {
  return page.waitForResponse(async (response) => {
    const url = new URL(response.url());
    if (url.pathname !== '/api/inbox' || response.request().method() !== 'GET' || !response.ok()) return false;
    const state = (await response.json()) as { sitting: { turn: { lines: string[] } | null } | null };
    return JSON.stringify(state.sitting?.turn?.lines) === JSON.stringify(lines);
  });
}

/** The page's own SSE connection, which every request the page makes names for the API to bind
 *  (NFR-4). */
async function connection(testEvents: TestEventCapture): Promise<string> {
  const connected = await testEvents.waitForEvent((event) => event.kind === 'sse' && event.event === 'connected');
  return (connected as { data: { requestId: string } }).data.requestId;
}

/** The next POST to `path`: its body, and the SSE connection it names. */
async function posted(page: Page, path: string): Promise<{ body: unknown; connection: string | null }> {
  const request = await page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === path);
  return { body: request.postDataJSON(), connection: await request.headerValue('x-sse-request-id') };
}

const ASK =
  "The reconciler's dedup misses a reworded observation whose headline differs only in its tense, so the " +
  'operator rules on the same friction twice in one week and the store keeps both of them';
const ONE_LETTER_ASK =
  "The dev instance's setup script regenerates its .env on every run and overwrites the port chosen " +
  'for the second checkout, so the two instances collide on the same port when both start';

/** The sorted inbox, as the skill writes it: a fenced block, and markdown around it. */
const SORTED = [
  '**The inbox**, sorted: three new cards and a hand-back.',
  '',
  '```',
  'New',
  '  EX-12  The webhook relay drops a delivery under load',
  '  EX-14  Docs: the install skill names the old path',
  `  X-3    ${ONE_LETTER_ASK}`,
  'Hand-backs',
  `  Slice:   EX-40  ${ASK}`,
  '```',
  '',
  'Questions:',
  '',
  '1. **EX-12**: fix now, or a card for slice `005`?',
  '   - It touches the relay only.',
  '     - No deploy change.',
  '2. EX-40 and X-3 ask for a ruling; see [the runbook](https://example.org/runbook).',
  '',
  'FR-28 stays as it is.',
].join('\n');

/** Where `needle`'s first character starts in the fenced line holding it, and where each line box
 *  the line wraps onto starts, in px. */
function fencedGeometry(page: Page, needle: string): Promise<{ at: number; boxes: number[] }> {
  return page.evaluate((needle) => {
    const line = Array.from(document.querySelectorAll('[data-testid="inbox.fenced"] .line')).find((l) =>
      l.textContent!.includes(needle),
    )!;
    const texts: Text[] = [];
    const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) texts.push(walker.currentNode as Text);
    let at = NaN;
    const starts = new Map<number, number>();
    for (const text of texts) {
      const range = document.createRange();
      const index = text.data.indexOf(needle);
      if (index >= 0 && Number.isNaN(at)) {
        range.setStart(text, index);
        range.setEnd(text, index + 1);
        at = range.getBoundingClientRect().left;
      }
      range.selectNodeContents(text);
      for (const rect of Array.from(range.getClientRects())) {
        if (!rect.width) continue;
        const top = Math.round(rect.top);
        starts.set(top, Math.min(starts.get(top) ?? Infinity, rect.left));
      }
    }
    return { at, boxes: [...starts.entries()].sort(([a], [b]) => a - b).map(([, left]) => left) };
  }, needle);
}

test.describe('Start', () => {
  test('without a scope: the inbox prompt opens the sitting, and the status line says what the session does, with no elapsed time', async ({ controller, inbox, page, testEvents }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }] });
    await inbox.open();
    await expect(inbox.start).toBeVisible();
    await expect(inbox.end).toHaveCount(0);

    const requestId = await connection(testEvents);
    const sent = posted(page, '/api/inbox/start');
    await inbox.begin();
    expect(await sent).toEqual({ body: { scope: '' }, connection: requestId });
    await expect(inbox.opening).toHaveText(/^\/dev:inbox · began \d\d:\d\d in env-1$/);
    await expect(inbox.working).toBeVisible();
    await expect(inbox.end).toBeVisible();

    await controller.emit('Sorting 4 cards');
    await expect(inbox.workingLines.last()).toHaveText('Sorting 4 cards');
    await controller.emit('Reading EX-3');
    await expect(inbox.workingLines.last()).toHaveText('Reading EX-3');

    await controller.finish('The inbox is empty.');
    await expect(inbox.sessionMessages).toHaveCount(1);
    await expect(inbox.sessionMessages.first()).toContainText('The inbox is empty.');
    await expect(inbox.working).toHaveCount(0);

    const reading = await controller.read();
    expect(reading.environments[0].sessions.map((s) => s.messages)).toEqual([['/dev:inbox']]);
  });

  test('with a scope: the scope is appended to the prompt; a stopped environment is started for the sitting', async ({ controller, inbox }) => {
    await controller.layOut({ environments: [{ env_id: 'env-2', status: 'stopped' }], turns: [{ held: true }] });
    await inbox.open();

    await inbox.begin('EX-12 EX-14');
    await expect(inbox.opening).toHaveText(
      /^\/dev:inbox EX-12 EX-14 · began \d\d:\d\d in env-2, started for this sitting$/,
    );

    await expect
      .poll(async () => (await controller.read()).environments[0].sessions.map((s) => s.messages))
      .toEqual([['/dev:inbox EX-12 EX-14']]);
  });

  test('an unconfigured inbox says so, and Start is refused', async ({ controller, inbox }) => {
    await controller.layOut({ inbox: false });
    await inbox.open();
    await expect(inbox.unconfigured).toContainText('The inbox is not configured');
    await expect(inbox.unconfigured.getByRole('button', { name: 'Start' })).toBeDisabled();
    await expect(inbox.start).toHaveCount(0);
    await expect(inbox.field).toHaveCount(0);
  });
});

test.describe('the composer', () => {
  test('Ctrl+Enter sends and Enter breaks the line, with nothing of its own on Shift+Enter or Alt+Enter; no placeholder; Send withheld while a turn is in flight, the field open', async ({ controller, inbox, page, testEvents }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }, { held: true }] });
    await inbox.open();
    await inbox.begin();
    await expect(inbox.sendButton).toBeDisabled();
    await expect(inbox.sendButton).toHaveAttribute('title', 'Wait for the session to answer');
    await expect(inbox.field).not.toHaveAttribute('placeholder');
    await controller.finish('Two questions.');
    await expect(inbox.sessionMessages).toHaveCount(1);
    await expect(inbox.sendButton).toHaveAttribute('title', 'Send (Ctrl+Enter)');
    await expect(inbox.field).not.toHaveAttribute('placeholder');

    // Enter and Shift+Enter break the line as the browser does; Alt+Enter does nothing.
    await inbox.field.click();
    await page.keyboard.type('line one');
    await page.keyboard.press('Enter');
    await page.keyboard.type('line two');
    await page.keyboard.press('Shift+Enter');
    await page.keyboard.type('line three');
    await page.keyboard.press('Alt+Enter');
    await page.keyboard.type('!');
    await expect(inbox.field).toHaveValue('line one\nline two\nline three!');
    await expect(inbox.operatorMessages).toHaveCount(0);

    const requestId = await connection(testEvents);
    const sent = posted(page, '/api/inbox/turns');
    await page.keyboard.press('Control+Enter');
    expect(await sent).toEqual({ body: { message: 'line one\nline two\nline three!' }, connection: requestId });
    await expect(inbox.operatorMessages).toHaveCount(1);
    await expect(inbox.field).toHaveValue('');
    await expect(inbox.working).toBeVisible();

    // The session works: Send is withheld, and the answer can be drafted meanwhile.
    await expect(inbox.sendButton).toBeDisabled();
    await expect(inbox.field).toBeEditable();
    await inbox.field.fill('Yes to both.');
    await inbox.field.press('Control+Enter');
    await expect(inbox.field).toHaveValue('Yes to both.');
    await expect(inbox.sendButton).toBeDisabled();
    await expect(inbox.operatorMessages).toHaveCount(1);

    await controller.finish('Recorded.');
    await expect(inbox.sessionMessages).toHaveCount(2);
    await expect(inbox.sendButton).toBeEnabled();
    await inbox.sendButton.click();
    await expect(inbox.operatorMessages).toHaveCount(2);

    await expect
      .poll(async () => (await controller.read()).environments[0].sessions[0].messages)
      .toEqual(['/dev:inbox', 'line one\nline two\nline three!', 'Yes to both.']);
  });

  test('the field grows with its text to eight lines, then scrolls', async ({ controller, inbox }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }] });
    await inbox.open();
    await inbox.begin();

    const measure = () =>
      inbox.field.evaluate((field: HTMLTextAreaElement) => {
        const style = getComputedStyle(field);
        return {
          height: field.getBoundingClientRect().height,
          line: parseFloat(style.lineHeight),
          scrolls: field.scrollHeight > field.clientHeight,
          overflowY: style.overflowY,
        };
      });
    const text = (count: number) => Array.from({ length: count }, (_, i) => `line ${i + 1}`).join('\n');

    await inbox.field.fill(text(1));
    const one = await measure();
    await inbox.field.fill(text(3));
    expect((await measure()).height).toBeCloseTo(one.height + 2 * one.line, 0);
    await inbox.field.fill(text(8));
    const eight = await measure();
    expect(eight.height).toBeCloseTo(one.height + 7 * one.line, 0);
    expect(eight.scrolls).toBe(false);
    await inbox.field.fill(text(12));
    const twelve = await measure();
    expect(twelve.height).toBeCloseTo(eight.height, 0);
    expect(twelve.scrolls).toBe(true);
    expect(twelve.overflowY).toBe('auto');
  });
});

test.describe('the working box', () => {
  test('grows to four lines as they arrive, the spinner on the last, the top line going as a new one comes and nothing marking it; each line on one line, cut on its own; a reload starts with the four', async ({ controller, inbox, page }) => {
    await page.setViewportSize({ width: 640, height: 800 });
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish('One question.');
    await inbox.send('Yes.');
    await expect(inbox.workingWaiting).toHaveText('Waiting for the session…');
    await expect(inbox.workingLines).toHaveCount(0);

    const lines = [
      'Reading EX-1',
      'Reading EX-2',
      'Reading EX-3',
      'Reading EX-4',
      'Recording the ruling on EX-1',
      // 80 characters, as the controller cuts a line: wider than the box at this width.
      `Bash: git -C /work/scratch/example log --oneline --since=yesterday -- docs/ src/…`,
    ];
    for (const [i, line] of lines.entries()) {
      await controller.emit(line);
      const shown = lines.slice(Math.max(0, i - 3), i + 1);
      await expect(inbox.workingLines).toHaveText(shown);
      await expect(inbox.workingWaiting).toHaveCount(0);
      // Nothing but the lines and the spinner.
      await expect(inbox.working).toHaveText(shown.join(''));
      await expect(inbox.working.locator('svg')).toHaveCount(1);
      // The spinner beside the last line.
      const spinner = (await inbox.workingSpinner.boundingBox())!;
      const last = (await inbox.workingLines.last().boundingBox())!;
      const middle = spinner.y + spinner.height / 2;
      expect(middle).toBeGreaterThan(last.y);
      expect(middle).toBeLessThan(last.y + last.height);
    }

    // Each line one line high, the long one cut with an ellipsis of its own, the others whole.
    const cut = await inbox.workingLines.evaluateAll((items) =>
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

    await inbox.reload();
    await expect(inbox.workingLines).toHaveText(lines.slice(-4));
  });

  test("back on the inbox screen mid-turn with no reload, it shows the sitting's lines once each and in order: the task channel's replay of lines heard before adds none", async ({ controller, inbox, appBar, page }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish('One question.');
    await inbox.send('Yes.');
    await controller.emit('Reading EX-1');
    await controller.emit('Reading EX-2');
    await expect(inbox.workingLines).toHaveText(['Reading EX-1', 'Reading EX-2']);
    await readHolding(page, ['Reading EX-1', 'Reading EX-2']);

    await appBar.triageTab.click();
    await appBar.expectScreen('triage');
    // The page's next read sets the lines whatever the replay added, so the screen is looked at
    // from the return until that read: back right after the pill's read, it is an interval away.
    await readHolding(page, ['Reading EX-1', 'Reading EX-2']);
    const reread = readHolding(page, ['Reading EX-1', 'Reading EX-2']).then(() => true);
    await appBar.inboxTab.click();
    await expect(inbox.working).toBeVisible();
    do {
      expect(await inbox.workingLines.allTextContents()).toEqual(['Reading EX-1', 'Reading EX-2']);
    } while (!(await Promise.race([reread, page.waitForTimeout(100).then(() => false)])));
    await controller.emit('Reading EX-3');
    await expect(inbox.workingLines).toHaveText(['Reading EX-1', 'Reading EX-2', 'Reading EX-3']);
  });

  test('each read of the sitting sets its lines, changed or not: a line the read held before its event came is shown once', async ({ controller, inbox, page }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish('One question.');
    await inbox.send('Yes.');
    await controller.emit('Reading EX-1');
    await expect(inbox.workingLines).toHaveText(['Reading EX-1']);

    // The API writes a line, then sends its event: a read between the two holds the line before
    // the page hears it. One read is made to answer so, ahead of the line.
    let ahead = true;
    // eslint-disable-next-line testing/no-route-mocks -- the read that lands between the API's write and its event cannot be timed
    await page.route(
      (url) => url.pathname === '/api/inbox',
      async (route) => {
        if (route.request().method() !== 'GET' || !ahead) return route.fallback();
        ahead = false;
        const response = await route.fetch();
        const state = (await response.json()) as { sitting: { turn: { lines: string[] } } };
        state.sitting.turn.lines = [...state.sitting.turn.lines, 'Reading EX-2'];
        // eslint-disable-next-line testing/no-route-mocks -- as above
        await route.fulfill({ response, json: state });
      },
    );
    await expect(inbox.workingLines).toHaveText(['Reading EX-1', 'Reading EX-2']);
    await controller.emit('Reading EX-2');
    await readHolding(page, ['Reading EX-1', 'Reading EX-2']);
    await expect(inbox.workingLines).toHaveText(['Reading EX-1', 'Reading EX-2']);
  });

  test('under a transcript taller than the screen, it keeps its newest line and the spinner in view as it grows, and leaves an operator who scrolled up where they are', async ({ controller, inbox, page }) => {
    await page.setViewportSize({ width: 1000, height: 600 });
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }, { held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish(Array.from({ length: 40 }, (_, i) => `Paragraph ${i + 1} of the answer.`).join('\n\n'));
    await inbox.send('Yes.');

    // Inside the scrolling area, above the composer.
    const inView = async (locator: Locator) => {
      const main = (await inbox.main.boundingBox())!;
      const box = (await locator.boundingBox())!;
      return box.y >= main.y && box.y + box.height <= main.y + main.height + 0.5;
    };
    for (const line of ['Reading EX-1', 'Reading EX-2', 'Reading EX-3', 'Reading EX-4']) {
      await controller.emit(line);
      await expect(inbox.workingLines.last()).toHaveText(line);
      await expect.poll(() => inView(inbox.workingLines.last())).toBe(true);
      await expect.poll(() => inView(inbox.workingSpinner)).toBe(true);
    }

    await controller.finish('Recorded.');
    await inbox.send('Go on.');
    await expect.poll(() => inView(inbox.workingWaiting)).toBe(true);
    await inbox.main.evaluate(
      (main) =>
        new Promise<void>((resolve) => {
          main.addEventListener('scroll', () => resolve(), { once: true });
          main.scrollTo({ top: 0 });
        }),
    );
    await controller.emit('Reading EX-5');
    await controller.emit('Reading EX-6');
    await expect(inbox.workingLines).toHaveCount(2);
    await page.waitForTimeout(500);
    expect(await inbox.main.evaluate((main) => main.scrollTop)).toBe(0);
  });
});

test.describe('the transcript', () => {
  test("each message's time under it, and no label for who wrote it", async ({ controller, inbox }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish('One question: EX-12?');
    await inbox.send('Yes.');

    for (const message of [inbox.sessionMessages.first(), inbox.operatorMessages.first()]) {
      const parts = message.locator(':scope > *');
      await expect(parts).toHaveCount(2);
      const time = message.getByTestId('inbox.message.time');
      await expect(time).toHaveText(/^\d\d:\d\d$/);
      await expect(time).toHaveAttribute('title', /^\d{4}-\d\d-\d\d \d\d:\d\d$/);
      const text = (await parts.first().boundingBox())!;
      const under = (await time.boundingBox())!;
      expect(under.y).toBeGreaterThanOrEqual(text.y + text.height);
    }
    await expect(inbox.transcript.getByText(/^(Session|You)$/)).toHaveCount(0);
  });

  test("a reply is every message of its turn in order, each a block set further apart than paragraphs and all alike, final or not, with one time under the lot; the result, the final messages joined, is not shown again", async ({ controller, inbox }) => {
    const narration = 'Let me pull the cards first.';
    const waited = 'Four lookups dispatched; I will wait for them.';
    const answer = 'Two new cards.\n\nOne question: EX-12, fix now?';
    await controller.layOut({
      environments: [{ env_id: 'env-1' }],
      turns: [
        {
          lines: [
            'Reading the inbox',
            { text: narration },
            'Bash: yt search EX',
            { text: waited, final: true },
            'Agent: look up EX-12',
            { text: answer, final: true },
          ],
          result: `${waited}\n\n${answer}`,
        },
      ],
    });
    await inbox.open();
    await inbox.begin();

    await expect(inbox.sessionMessages).toHaveCount(1);
    const reply = inbox.sessionMessages.first();
    const blocks = reply.getByTestId('inbox.message.block');
    await expect(blocks).toHaveText([narration, waited, /^Two new cards\.\s*One question: EX-12, fix now\?$/]);
    await expect(blocks.nth(2).locator('p')).toHaveText(['Two new cards.', 'One question: EX-12, fix now?']);
    await expect(inbox.transcript.getByText(waited, { exact: true })).toHaveCount(1);
    await expect(inbox.transcript.getByText('Two new cards.', { exact: true })).toHaveCount(1);

    // The reply's box holds its blocks and nothing else, its one time under it.
    const parts = reply.locator(':scope > *');
    await expect(parts).toHaveCount(2);
    await expect(parts.first().locator(':scope > *')).toHaveCount(3);
    const time = reply.getByTestId('inbox.message.time');
    await expect(time).toHaveCount(1);
    const last = (await blocks.last().boundingBox())!;
    expect((await time.boundingBox())!.y).toBeGreaterThanOrEqual(last.y + last.height);

    const layout = await reply.evaluate((el) => {
      const blocks = Array.from(el.querySelectorAll('[data-testid="inbox.message.block"]'));
      const boxes = blocks.map((block) => block.getBoundingClientRect());
      const [first, second] = Array.from(blocks[2].querySelectorAll(':scope > p')).map((p) => p.getBoundingClientRect());
      const look = (node: Element) => {
        const style = getComputedStyle(node);
        return [style.color, style.opacity, style.fontStyle, style.fontWeight, style.fontSize, style.backgroundColor, style.borderStyle, style.paddingLeft].join(' ');
      };
      return {
        gaps: boxes.slice(1).map((box, i) => box.top - boxes[i].bottom),
        paragraph: second.top - first.bottom,
        looks: blocks.map((block) => `${block.className} | ${look(block)} | ${look(block.querySelector('p')!)}`),
      };
    });
    expect(layout.paragraph).toBeGreaterThan(0);
    for (const gap of layout.gaps) expect(gap).toBeGreaterThan(layout.paragraph);
    expect(new Set(layout.looks).size).toBe(1);
  });

  test("while the turn runs, the working box shows its log lines and no message of the turn, a reload included; the reply lands whole at the turn's end", async ({ controller, inbox, page, request, backendUrl }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish('One question.');
    await inbox.send('Yes.');

    await controller.emit({ text: 'Let me read your answers.' });
    await controller.emit('Reading EX-1');
    await controller.emit({ text: 'EX-1 recorded as yes.', final: true });
    await controller.emit('Reading EX-2');
    await expect(inbox.workingLines).toHaveText(['Reading EX-1', 'Reading EX-2']);
    await readHolding(page, ['Reading EX-1', 'Reading EX-2']);
    const unshown = page.getByText(/Let me read your answers|recorded as yes/);
    await expect(inbox.workingLines).toHaveText(['Reading EX-1', 'Reading EX-2']);
    await expect(inbox.sessionMessages).toHaveCount(1);
    await expect(unshown).toHaveCount(0);
    const turn = (await inboxState(request, backendUrl)).sitting!.turn!;
    expect(Object.keys(turn).sort()).toEqual(['began', 'lines', 'task']);

    await inbox.reload();
    await expect(inbox.workingLines).toHaveText(['Reading EX-1', 'Reading EX-2']);
    await expect(inbox.sessionMessages).toHaveCount(1);
    await expect(unshown).toHaveCount(0);

    await controller.finish('EX-1 recorded as yes.');
    await expect(inbox.sessionMessages).toHaveCount(2);
    await expect(inbox.working).toHaveCount(0);
    await expect(inbox.sessionMessages.last().getByTestId('inbox.message.block')).toHaveText([
      'Let me read your answers.',
      'EX-1 recorded as yes.',
    ]);
  });

  test("a turn whose stream carries no message is shown by its result's text, one block, as a reply always was", async ({ controller, inbox }) => {
    await controller.layOut({
      environments: [{ env_id: 'env-1' }],
      turns: [{ lines: ['Reading EX-1'], result: 'Recorded.\n\nNothing else to rule on.' }],
    });
    await inbox.open();
    await inbox.begin();

    const blocks = inbox.sessionMessages.first().getByTestId('inbox.message.block');
    await expect(blocks).toHaveCount(1);
    await expect(blocks.locator('p')).toHaveText(['Recorded.', 'Nothing else to rule on.']);
  });

  test("a turn that ends in an error shows its messages, then the error once: the CLI's, which a final message carries, and the worker's own, which none does", async ({ controller, inbox }) => {
    const overloaded = 'The model is overloaded; try again later.';
    const ended = 'session ended before a terminal result';
    await controller.layOut({
      environments: [{ env_id: 'env-1' }],
      turns: [
        {
          lines: [{ text: 'Recording the rulings.' }, 'Bash: yt comment EX-12', { text: overloaded, final: true }],
          result: overloaded,
          is_error: true,
        },
        { lines: [{ text: 'Let me check EX-12.' }, 'Reading EX-12'], result: ended, is_error: true },
      ],
    });
    await inbox.open();
    await inbox.begin();
    await expect(inbox.sessionMessages.first().getByTestId('inbox.message.block')).toHaveText([
      'Recording the rulings.',
      overloaded,
    ]);

    await inbox.send('Try again.');
    await expect(inbox.sessionMessages).toHaveCount(2);
    await expect(inbox.sessionMessages.last().getByTestId('inbox.message.block')).toHaveText([
      'Let me check EX-12.',
      ended,
    ]);
    await expect(inbox.transcript.getByText(overloaded, { exact: true })).toHaveCount(1);
    await expect(inbox.transcript.getByText(ended, { exact: true })).toHaveCount(1);
  });

  test('the sorted inbox in monospace, its alignment kept and a wrapped line under its own text; the markdown around it; every card id linked to the tracker', async ({ controller, inbox, page, request, backendUrl }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish(SORTED);
    const message = inbox.sessionMessages.first();
    await expect(message).toBeVisible();

    const fenced = message.getByTestId('inbox.fenced');
    expect(await fenced.evaluate((el) => getComputedStyle(el).fontFamily)).toMatch(/monospace/);
    expect(await message.locator('p').first().evaluate((el) => getComputedStyle(el).fontFamily)).not.toMatch(/monospace/);
    expect(await fenced.locator('.line').allTextContents()).toEqual([
      'New',
      '  EX-12  The webhook relay drops a delivery under load',
      '  EX-14  Docs: the install skill names the old path',
      `  X-3    ${ONE_LETTER_ASK}`,
      'Hand-backs',
      `  Slice:   EX-40  ${ASK}`,
    ]);
    expect(await fenced.locator('.line').first().evaluate((el) => getComputedStyle(el).whiteSpace)).toBe('pre-wrap');

    // The columns line up: each card's ask starts where the one above it does.
    const webhook = await fencedGeometry(page, 'The webhook');
    const docs = await fencedGeometry(page, 'Docs:');
    expect(Math.abs(webhook.at - docs.at)).toBeLessThan(0.5);
    // The long ask wraps, and its continuation starts under the ask, not under the line's start.
    const slice = await fencedGeometry(page, "The reconciler's");
    expect(slice.boxes.length).toBeGreaterThan(1);
    expect(Math.abs(slice.boxes[1] - slice.at)).toBeLessThan(1);
    expect(slice.boxes[1]).toBeGreaterThan(slice.boxes[0] + 1);
    // A one-letter project key is a card id too (FN-51): its ask lines up and wraps under itself.
    const oneLetter = await fencedGeometry(page, "The dev instance's");
    expect(Math.abs(oneLetter.at - webhook.at)).toBeLessThan(0.5);
    expect(oneLetter.boxes.length).toBeGreaterThan(1);
    expect(Math.abs(oneLetter.boxes[1] - oneLetter.at)).toBeLessThan(1);

    await expect(message.locator('strong').first()).toHaveText('The inbox');
    await expect(message.locator('ol > li')).toHaveCount(2);
    await expect(message.locator('ol > li > ul > li > ul > li')).toHaveText('No deploy change.');
    await expect(message.locator('code')).toHaveText('005');
    await expect(message.getByRole('link', { name: 'the runbook' })).toHaveAttribute('href', 'https://example.org/runbook');
    await expect(message.locator('p').last()).toHaveText('FR-28 stays as it is.');

    const { tracker } = await inboxState(request, backendUrl);
    expect(tracker).toBeTruthy();
    const cards = message.getByRole('link', { name: /^[A-Z]+-\d+$/ });
    await expect(cards).toHaveText(['EX-12', 'EX-14', 'X-3', 'EX-40', 'EX-12', 'EX-40', 'X-3']);
    for (const card of await cards.all()) {
      await expect(card).toHaveAttribute('href', `${tracker}${await card.textContent()}`);
    }
    await expect(message.getByRole('link', { name: 'FR-28' })).toHaveCount(0);
  });

  test("a reload mid-turn rejoins the turn, starting with the turn's last four lines, and gets its result", async ({ controller, inbox, request, backendUrl }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.emit('Reading the inbox');
    await expect(inbox.workingLines.last()).toHaveText('Reading the inbox');
    const held = (await inboxState(request, backendUrl)).sitting!.turn!.lines;
    expect(held).toHaveLength(4);
    expect(held[3]).toBe('Reading the inbox');

    await inbox.reload();
    await expect(inbox.opening).toBeVisible();
    await expect(inbox.workingLines).toHaveText(held);
    await expect(inbox.sendButton).toBeDisabled();

    await controller.emit('Sorting 3 cards');
    await expect(inbox.workingLines).toHaveText([...held.slice(1), 'Sorting 3 cards']);
    await controller.finish('Sorted.');
    await expect(inbox.sessionMessages).toHaveCount(1);
    await expect(inbox.working).toHaveCount(0);
  });

  test('a finished turn is not followed again while a stale read still names it: the page fetches on its timer, not in a loop', async ({ controller, inbox, page, request, backendUrl, testEvents }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.emit('Reading the inbox');
    await expect(inbox.workingLines.last()).toHaveText('Reading the inbox');
    const midTurn = await inboxState(request, backendUrl);
    const turn = midTurn.sitting!.turn!;

    // The API writes a turn's result before its task completes, so no read it serves names a
    // finished turn: only a follow that fails outright stays named, until a takeover a minute
    // later. Here every read answers the sitting mid-turn, its log lines moving, so the page sees
    // the turn it saw finish named again on each read.
    let reads = 0;
    // eslint-disable-next-line testing/no-route-mocks -- the API cannot be made to serve a finished turn's sitting
    await page.route(
      (url) => url.pathname === '/api/inbox',
      async (route) => {
        if (route.request().method() !== 'GET') return route.fallback();
        reads += 1;
        // eslint-disable-next-line testing/no-route-mocks -- as above: the stale read is the case under test
        await route.fulfill({
          json: { ...midTurn, sitting: { ...midTurn.sitting, turn: { ...turn, lines: [`Read ${reads}`] } } },
        });
      },
    );
    await controller.finish('Sorted.');
    await testEvents.waitForEvent(
      (event) => event.kind === 'sse' && event.event === 'task_completed' && (event.data as { taskId?: string })?.taskId === turn.task,
      { timeout: 10000 },
    );

    await page.waitForTimeout(500);
    const before = reads;
    await page.waitForTimeout(4000);
    expect(reads - before).toBeLessThanOrEqual(4);
  });
});

test.describe('a lost sitting', () => {
  test('keeps its transcript with a line saying so; Send is withdrawn and a new Start clears it', async ({ controller, inbox }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }, { held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish('One question.');
    await inbox.send('Yes.');
    await expect(inbox.working).toBeVisible();

    await controller.lose('session');
    await expect(inbox.lost).toContainText(/^Lost at \d\d:\d\d\. .+ What the session wrote on the tracker stands/);
    await expect(inbox.opening).toBeVisible();
    await expect(inbox.sessionMessages).toHaveText([/One question\./]);
    await expect(inbox.operatorMessages).toHaveText([/Yes\./]);
    await expect(inbox.working).toHaveCount(0);
    await expect(inbox.field).toHaveCount(0);
    await expect(inbox.sendButton).toHaveCount(0);
    await expect(inbox.restart).toContainText('The session is gone, and Send with it.');

    await inbox.restartStart.click();
    await expect(inbox.lost).toHaveCount(0);
    await expect(inbox.sessionMessages).toHaveCount(0);
    await expect(inbox.operatorMessages).toHaveCount(0);
    await expect(inbox.opening).toHaveText(/^\/dev:inbox · began \d\d:\d\d in env-1$/);
    await expect(inbox.field).toBeVisible();
  });
});

test.describe('a sitting the idle time rested', () => {
  test('stays live, its transcript, Send and the steady Inbox pill kept; the next Send starts the environment again and resumes the session (FN-52)', async ({ controller, inbox, appBar }) => {
    await controller.layOut({
      environments: [{ env_id: 'env-1', status: 'stopped' }],
      turns: [{ held: true }, { held: true }, { held: true }],
    });
    await inbox.open();
    await inbox.begin();
    await controller.finish('One question.');
    await inbox.send('Yes.');
    await controller.finish('Recorded.');
    await expect(inbox.sessionMessages).toHaveCount(2);

    await controller.rest();
    await expect
      .poll(async () => (await controller.read()).environments)
      .toEqual([{ env_id: 'env-1', status: 'stopped', sessions: [] }]);
    await inbox.reload();
    await expect(inbox.opening).toHaveText(/^\/dev:inbox · began \d\d:\d\d in env-1$/);
    await expect(inbox.sessionMessages).toHaveText([/One question\./, /Recorded\./]);
    await expect(inbox.operatorMessages).toHaveText([/Yes\./]);
    await expect(inbox.lost).toHaveCount(0);
    await expect(inbox.restart).toHaveCount(0);
    await expect(inbox.field).toBeVisible();
    await appBar.expectPill(appBar.inboxPill, 'waiting', /waits for your answer/);

    await inbox.send('And EX-3?');
    await expect(inbox.working).toBeVisible();
    await controller.finish('EX-3 is closed.');
    await expect(inbox.sessionMessages).toHaveText([/One question\./, /Recorded\./, /EX-3 is closed\./]);
    await expect(inbox.operatorMessages).toHaveText([/Yes\./, /And EX-3\?/]);
    await expect(inbox.opening).toHaveText(/^\/dev:inbox · began \d\d:\d\d in env-1, started for this sitting$/);
    const [env] = (await controller.read()).environments;
    expect(env.status).toBe('running');
    // A new session, the conversation resumed in it: it was sent the new turn alone.
    expect(env.sessions.map((s) => s.messages)).toEqual([['And EX-3?']]);
  });

  test('End clears it', async ({ controller, inbox, request, backendUrl }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish('Sorted.');
    await expect(inbox.sessionMessages).toHaveCount(1);
    await controller.rest();
    await inbox.reload();
    await expect(inbox.sessionMessages).toHaveCount(1);

    await inbox.end.click();
    await inbox.endConfirm.click();
    await expect(inbox.start).toBeVisible();
    await expect(inbox.transcript).toHaveCount(0);
    expect((await inboxState(request, backendUrl)).sitting).toBeNull();
  });
});

test.describe('End', () => {
  test('asks first, then ends the session, stops the environment it started and clears the sitting', async ({ controller, inbox, page, request, backendUrl }) => {
    await controller.layOut({ environments: [{ env_id: 'env-1', status: 'stopped' }], turns: [{ held: true }] });
    await inbox.open();
    await inbox.begin();
    await controller.finish('Sorted.');
    await expect(inbox.sessionMessages).toHaveCount(1);

    await inbox.end.click();
    await expect(inbox.endDialog).toContainText('the transcript goes with it');
    await expect(inbox.endDialog).toContainText('env-1 was started for this sitting and is stopped, unless another session is working in it.');
    await page.keyboard.press('Escape');
    await expect(inbox.endDialog).toHaveCount(0);
    await expect(inbox.sessionMessages).toHaveCount(1);

    await inbox.end.click();
    await inbox.endConfirm.click();
    await expect(inbox.start).toBeVisible();
    await expect(inbox.transcript).toHaveCount(0);
    await expect(inbox.end).toHaveCount(0);
    await expect(inbox.toasts).toHaveCount(0);

    expect((await inboxState(request, backendUrl)).sitting).toBeNull();
    await expect
      .poll(async () => (await controller.read()).environments)
      .toEqual([{ env_id: 'env-1', status: 'stopped', sessions: [] }]);
  });
});

test('the inbox screen meets WCAG AA in both themes: Start, a sitting mid-turn, a lost one', async ({ controller, inbox, page }) => {
  await controller.layOut({ environments: [{ env_id: 'env-1' }], turns: [{ held: true }, { held: true }, { held: true }] });
  await inbox.open();

  const views: Record<string, Failure[]> = {};
  for (const theme of ['light', 'dark'] as const) {
    await inbox.setTheme(theme);
    views[`start, ${theme}`] = await contrastFailures(page);
  }
  await inbox.begin();
  await controller.finish(SORTED);
  await inbox.send('Yes to both.');
  await controller.emit('Recording the rulings');
  await expect(inbox.workingLines).toHaveText(['Recording the rulings']);
  for (const theme of ['light', 'dark'] as const) {
    await inbox.setTheme(theme);
    views[`mid-turn, ${theme}`] = await contrastFailures(page);
  }
  await controller.lose('session');
  await expect(inbox.lost).toBeVisible();
  for (const theme of ['light', 'dark'] as const) {
    await inbox.setTheme(theme);
    views[`lost, ${theme}`] = await contrastFailures(page);
  }

  expect(Object.fromEntries(Object.entries(views).filter(([, failures]) => failures.length))).toEqual({});
});
