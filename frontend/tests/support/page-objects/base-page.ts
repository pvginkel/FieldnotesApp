import { Page, Locator, expect } from '@playwright/test';

/**
 * Base page object with common patterns and utilities.
 * All page objects should extend this base class.
 */
export class BasePage {
  constructor(protected readonly page: Page) {}

  get playwrightPage(): Page {
    return this.page;
  }

  async goto(path: string = ''): Promise<void> {
    await this.page.goto(path);
  }

  async waitForPageLoad(options?: { timeout?: number }): Promise<void> {
    await this.page.waitForLoadState('networkidle', options);
  }

  getUrl(): string {
    return this.page.url();
  }

  getButton(text: string | RegExp): Locator {
    return this.page.getByRole('button', { name: text });
  }

  getLink(text: string | RegExp): Locator {
    return this.page.getByRole('link', { name: text });
  }

  async waitForToast(options?: { timeout?: number }): Promise<Locator> {
    const toast = this.page.getByTestId('app-shell.toast.item').first();
    await toast.waitFor({ state: 'visible', ...options });
    return toast;
  }

  async expectToast(text: string | RegExp, options?: { timeout?: number }): Promise<void> {
    const toast = await this.waitForToast(options);
    await expect(toast).toContainText(text, options);
  }
}
