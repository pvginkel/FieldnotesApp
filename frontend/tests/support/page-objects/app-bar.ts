import { expect, type Locator, type Page } from '@playwright/test';

/** The app bar in both screens' headers (src/components/shell/): the screen tabs and the pills. */
export class AppBar {
  readonly triageTab: Locator;
  readonly inboxTab: Locator;
  readonly inboxPill: Locator;
  readonly actionerPill: Locator;

  constructor(private readonly page: Page) {
    this.triageTab = page.getByTestId('app-shell.screens.triage');
    this.inboxTab = page.getByTestId('app-shell.screens.inbox');
    this.inboxPill = page.getByTestId('app-shell.pill.inbox');
    this.actionerPill = page.getByTestId('app-shell.pill.actioner');
  }

  /** The pill shows in this state (`working`, `waiting`, `lost`, `ended` or `failed`), its tooltip
   *  saying what. */
  async expectPill(pill: Locator, state: string, title: RegExp): Promise<void> {
    await expect(pill).toHaveAttribute('data-state', state);
    await expect(pill).toHaveAttribute('title', title);
  }

  /** The tab marked as the screen shown, and the other one not. */
  async expectScreen(screen: 'triage' | 'inbox'): Promise<void> {
    const [shown, other] = screen === 'triage' ? [this.triageTab, this.inboxTab] : [this.inboxTab, this.triageTab];
    await expect(shown).toHaveClass(/\bactive\b/);
    await expect(other).not.toHaveClass(/\bactive\b/);
  }

  /** Both pills' states fetched, for a test that reads one as not shown. */
  async fetched(open: () => Promise<void>): Promise<void> {
    const answered = (path: string) =>
      this.page.waitForResponse((response) => new URL(response.url()).pathname === path && response.ok());
    await Promise.all([answered('/api/inbox'), answered('/api/triage/actioner'), open()]);
  }
}
