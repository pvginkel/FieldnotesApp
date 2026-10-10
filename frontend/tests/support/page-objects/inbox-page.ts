import { expect, type Locator, type Page } from '@playwright/test';

/** The inbox screen (src/components/inbox/): Start, the transcript, the composer and End. */
export class InboxPage {
  readonly header: Locator;
  readonly main: Locator;
  readonly start: Locator;
  readonly scope: Locator;
  readonly startButton: Locator;
  readonly unconfigured: Locator;
  readonly transcript: Locator;
  readonly opening: Locator;
  readonly sessionMessages: Locator;
  readonly operatorMessages: Locator;
  readonly working: Locator;
  readonly workingLog: Locator;
  readonly lost: Locator;
  readonly field: Locator;
  readonly sendButton: Locator;
  readonly restart: Locator;
  readonly restartStart: Locator;
  readonly end: Locator;
  readonly endDialog: Locator;
  readonly endConfirm: Locator;
  readonly toasts: Locator;

  constructor(
    private readonly page: Page,
    private readonly frontendUrl: string,
  ) {
    this.header = page.getByTestId('inbox.header');
    this.main = page.getByTestId('inbox.main');
    this.start = page.getByTestId('inbox.start');
    this.scope = page.getByTestId('inbox.start.scope');
    this.startButton = page.getByTestId('inbox.start.submit');
    this.unconfigured = page.getByTestId('inbox.unconfigured');
    this.transcript = page.getByTestId('inbox.transcript');
    this.opening = page.getByTestId('inbox.opening');
    this.sessionMessages = page.getByTestId('inbox.message.session');
    this.operatorMessages = page.getByTestId('inbox.message.operator');
    this.working = page.getByTestId('inbox.working');
    this.workingLog = page.getByTestId('inbox.working.log');
    this.lost = page.getByTestId('inbox.lost');
    this.field = page.getByTestId('inbox.composer.text');
    this.sendButton = page.getByTestId('inbox.composer.send');
    this.restart = page.getByTestId('inbox.restart');
    this.restartStart = page.getByTestId('inbox.restart.start');
    this.end = page.getByTestId('inbox.header.end');
    this.endDialog = page.getByTestId('inbox.end-dialog');
    this.endConfirm = page.getByTestId('inbox.end-dialog.end');
    this.toasts = page.getByTestId('triage.toast');
  }

  /** Open the page and wait for the sitting: Start, or the transcript. */
  async open(): Promise<void> {
    await this.page.goto(`${this.frontendUrl}/inbox`);
    await this.loaded();
  }

  async reload(): Promise<void> {
    await this.page.reload();
    await this.loaded();
  }

  private async loaded(): Promise<void> {
    await expect(this.header).toBeVisible();
    await expect(this.start.or(this.unconfigured).or(this.transcript)).toBeVisible();
  }

  /** Start, with the scope typed when one is given; the sitting's opening line shown. */
  async begin(scope = ''): Promise<void> {
    if (scope) await this.scope.fill(scope);
    await this.startButton.click();
    await expect(this.opening).toBeVisible();
  }

  /** The operator's answer typed and sent with Enter. */
  async send(text: string): Promise<void> {
    await this.field.fill(text);
    await this.field.press('Enter');
    await expect(this.operatorMessages.last()).toContainText(text);
  }

  async setTheme(theme: 'light' | 'dark'): Promise<void> {
    await this.page.getByTestId('app-shell.topbar.user').click();
    await this.page.getByTestId('triage.prefs.theme').selectOption(theme);
    await this.page.getByTestId('app-shell.topbar.user').click();
    await expect(this.page.locator('html')).toHaveAttribute('data-theme', theme);
  }
}
