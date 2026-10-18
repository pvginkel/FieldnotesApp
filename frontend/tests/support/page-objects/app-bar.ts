import { expect, type Locator, type Page } from '@playwright/test';

/** The app bar in both screens' headers (src/components/shell/): the screen tabs, the pills, and
 *  the dialog the red Actioner pill opens. */
export class AppBar {
  readonly triageTab: Locator;
  readonly inboxTab: Locator;
  readonly inboxPill: Locator;
  readonly actionerPill: Locator;
  readonly failure: Locator;
  readonly failureWhat: Locator;
  readonly failureWhen: Locator;
  readonly failureReason: Locator;
  readonly failureClose: Locator;
  readonly failureCancel: Locator;

  constructor(private readonly page: Page) {
    this.triageTab = page.getByTestId('app-shell.screens.triage');
    this.inboxTab = page.getByTestId('app-shell.screens.inbox');
    this.inboxPill = page.getByTestId('app-shell.pill.inbox');
    this.actionerPill = page.getByTestId('app-shell.pill.actioner');
    this.failure = page.getByTestId('app-shell.actioner-failure');
    this.failureWhat = page.getByTestId('app-shell.actioner-failure.what');
    this.failureWhen = page.getByTestId('app-shell.actioner-failure.when');
    this.failureReason = page.getByTestId('app-shell.actioner-failure.reason');
    this.failureClose = page.getByTestId('app-shell.actioner-failure.close');
    this.failureCancel = page.getByTestId('app-shell.actioner-failure.cancel');
  }

  /** The pill shows in this state (`working`, `waiting`, `lost` or `failed`), its tooltip saying
   *  what. */
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
