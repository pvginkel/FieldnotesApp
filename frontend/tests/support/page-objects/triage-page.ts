import { expect, type Locator, type Page } from '@playwright/test';
import type { Verb } from '../triage-store';

/** The triage screen (src/components/triage/): the stack, the ruling pane and the header. */
export class TriagePage {
  readonly card: Locator;
  readonly headline: Locator;
  readonly cardId: Locator;
  readonly store: Locator;
  readonly pane: Locator;
  readonly note: Locator;
  readonly noteClear: Locator;
  readonly previousButton: Locator;
  readonly nextButton: Locator;
  readonly segments: Locator;
  readonly headerSubmit: Locator;
  readonly finish: Locator;
  readonly finishSubmit: Locator;
  readonly finishRestart: Locator;
  readonly finishPrevious: Locator;
  readonly empty: Locator;
  readonly toasts: Locator;
  readonly keys: Locator;
  readonly main: Locator;

  constructor(
    private readonly page: Page,
    private readonly frontendUrl: string,
  ) {
    this.card = page.getByTestId('triage.card');
    this.headline = this.card.locator('.headline');
    this.cardId = page.getByTestId('triage.card.id');
    // What the store says now: a tooltip over the card's id, rendered outside the card.
    this.store = page.getByTestId('triage.card.store');
    this.pane = page.getByTestId('triage.pane');
    this.note = page.getByTestId('triage.note');
    this.noteClear = page.getByTestId('triage.note.clear');
    this.previousButton = page.getByTestId('triage.previous');
    this.nextButton = page.getByTestId('triage.next');
    this.segments = page.getByTestId('triage.progress.segment');
    this.headerSubmit = page.getByTestId('triage.header.submit');
    this.finish = page.getByTestId('triage.finish');
    this.finishSubmit = page.getByTestId('triage.finish.submit');
    this.finishRestart = page.getByTestId('triage.finish.restart');
    this.finishPrevious = page.getByTestId('triage.finish.previous');
    this.empty = page.getByTestId('triage.empty');
    this.toasts = page.getByTestId('triage.toast');
    this.keys = page.getByTestId('triage.keys');
    this.main = page.getByTestId('triage.main');
  }

  /** Open the page and wait for the queue: a card, the finish card or the empty page. */
  async open(): Promise<void> {
    await this.page.goto(this.frontendUrl);
    await this.loaded();
  }

  async reload(): Promise<void> {
    await this.page.reload();
    await this.loaded();
  }

  private async loaded(): Promise<void> {
    await expect(this.card.or(this.finish).or(this.empty)).toBeVisible();
  }

  verb(verb: Verb): Locator {
    return this.page.getByTestId(`triage.verb.${verb}`);
  }

  /** The card shown is the one on this observation. */
  async expectCard(id: string): Promise<void> {
    await expect(this.card).toHaveAttribute('data-observation', id);
  }

  /** The progress bar's segments' states, in stack order: `open`, `ruled` or `skipped`. */
  async expectSegments(states: string[]): Promise<void> {
    await expect(this.segments).toHaveCount(states.length);
    for (const [i, state] of states.entries()) {
      await expect(this.segments.nth(i)).toHaveAttribute('data-state', state);
    }
  }

  /** The verb shown selected in the pane, or none. */
  async expectVerb(verb: Verb | null): Promise<void> {
    for (const other of ['yes', 'no', 'later'] as const) {
      await expect(this.verb(other)).toHaveAttribute('aria-pressed', String(other === verb));
    }
  }

  async press(key: string): Promise<void> {
    await this.page.keyboard.press(key);
  }

  /** The theme preference, from the avatar's menu. */
  async setTheme(theme: 'light' | 'dark'): Promise<void> {
    await this.page.getByTestId('app-shell.topbar.user').click();
    await this.page.getByTestId('triage.prefs.theme').selectOption(theme);
    await this.page.getByTestId('app-shell.topbar.user').click();
    await expect(this.page.locator('html')).toHaveAttribute('data-theme', theme);
  }
}
