import { expect, type Locator, type Page } from '@playwright/test';

/** The inbox screen (src/components/inbox/). */
export class InboxPage {
  readonly header: Locator;

  constructor(
    private readonly page: Page,
    private readonly frontendUrl: string,
  ) {
    this.header = page.getByTestId('inbox.header');
  }

  async open(): Promise<void> {
    await this.page.goto(`${this.frontendUrl}/inbox`);
    await expect(this.header).toBeVisible();
  }
}
