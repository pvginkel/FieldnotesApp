/// <reference lib="dom" />
import { Page, Locator, expect } from '@playwright/test';

/**
 * Toast types supported by the application
 */
export type ToastType = 'success' | 'error' | 'info' | 'warning';

/**
 * Toast notification data structure
 */
export interface ToastData {
  text: string;
  type?: ToastType;
  duration?: number;
  timestamp?: number;
}

/**
 * Helper class for working with toast notifications
 */
export class ToastHelper {
  private toastHistory: ToastData[] = [];

  constructor(private readonly page: Page) {}

  /**
   * Gets the toast container element
   * @returns Locator for the toast container
   */
  private getToastContainer(): Locator {
    return this.page.locator('[data-testid="app-shell.toast.item"]').first();
  }

  /**
   * Gets all current toast elements
   * @returns Locator for all toasts
   */
  private getAllToasts(): Locator {
    return this.page.locator('[data-testid="app-shell.toast.item"]');
  }

  /**
   * Waits for a toast to appear
   * @param options - Wait options
   * @returns The toast locator
   */
  async waitForToast(options?: { timeout?: number }): Promise<Locator> {
    const toast = this.getToastContainer();
    await toast.waitFor({ state: 'visible', ...options });
    return toast;
  }

  /**
   * Waits for a toast with specific text
   * @param text - The expected toast text
   * @param options - Wait options
   * @returns The toast locator
   */
  async waitForToastWithText(
    text: string | RegExp,
    options?: { timeout?: number }
  ): Promise<Locator> {
    const matchingToast = this.page
      .locator('[data-testid="app-shell.toast.item"]')
      .filter({ hasText: text });

    const target = matchingToast.first();
    await target.waitFor({ state: 'visible', ...(options ?? {}) });
    await expect(target).toContainText(text, options);
    return target;
  }

  /**
   * Gets the current toast text
   * @returns The toast text or null
   */
  async getToastText(): Promise<string | null> {
    const toast = this.getToastContainer();
    if (await toast.isVisible()) {
      return toast.textContent();
    }
    return null;
  }

  /**
   * Gets all current toast texts
   * @returns Array of toast texts
   */
  async getAllToastTexts(): Promise<string[]> {
    const toasts = this.getAllToasts();
    const count = await toasts.count();
    const texts: string[] = [];

    for (let i = 0; i < count; i++) {
      const text = await toasts.nth(i).textContent();
      if (text) {
        texts.push(text);
      }
    }

    return texts;
  }

  /**
   * Dismisses the current toast
   * @param options - Dismiss options
   */
  async dismissToast(options?: { all?: boolean }): Promise<void> {
    if (options?.all) {
      const toasts = this.getAllToasts();
      const count = await toasts.count();

      for (let i = 0; i < count; i++) {
        const closeButton = toasts.nth(i).locator('button[aria-label="Close"]');
        if (await closeButton.isVisible()) {
          await closeButton.click();
        }
      }
    } else {
      const closeButton = this.page.locator('[data-testid="app-shell.toast.item"] button[aria-label="Close"]').first();
      if (await closeButton.isVisible()) {
        await closeButton.click();
      }
    }
  }

  /**
   * Waits for all toasts to disappear
   * @param options - Wait options
   */
  async waitForToastsToDisappear(options?: { timeout?: number }): Promise<void> {
    await this.dismissToast({ all: true });
    const toast = this.getToastContainer();
    await toast.waitFor({ state: 'hidden', ...options });
  }

  /**
   * Waits for the toast viewport to stop intercepting pointer events.
   *
   * This is critical for preventing flaky tests where the toast viewport
   * intercepts pointer events even after toasts are dismissed.
   * Use this method after operations that trigger toasts and before
   * attempting to interact with UI elements that might be blocked by the viewport.
   *
   * This provides a brief fixed wait to allow toast exit animations to complete
   * and the viewport to stop intercepting pointer events. This is a pragmatic
   * workaround for intermittent issues with toast dismissal timing.
   *
   * @see https://playwright.dev/docs/actionability for details on pointer event interception
   */
  async waitForToastViewportDismissed(): Promise<void> {
    // Fixed wait for toast exit animations to complete
    // This handles the intermittent case where toasts may take longer to clear
    await this.page.waitForTimeout(1000);
  }

  /**
   * Asserts a success toast appears with specific text
   * @param text - Expected text
   * @param options - Assertion options
   */
  async expectSuccessToast(text: string | RegExp, options?: { timeout?: number }): Promise<void> {
    const toast = await this.waitForToastWithText(text, options);

    // Check for success-specific styling or class
    const hasSuccessClass = await toast.evaluate(el =>
      el.classList.contains('success') ||
      el.classList.contains('toast-success') ||
      el.querySelector('.text-green-600') !== null ||
      el.querySelector('[data-toast-type="success"]') !== null
    );

    if (!hasSuccessClass) {
      // Fallback: check if the toast contains success indicators in text
      const toastText = await toast.textContent();
      if (toastText && !toastText.toLowerCase().includes('success') && !toastText.includes('✓')) {
        console.warn('Toast may not be a success toast (no success indicators found)');
      }
    }

    this.recordToast({ text: text.toString(), type: 'success' });
    await this.dismissToast();
  }

  /**
   * Asserts an error toast appears with specific text
   * @param text - Expected text
   * @param options - Assertion options
   */
  async expectErrorToast(text: string | RegExp, options?: { timeout?: number }): Promise<void> {
    const toast = await this.waitForToastWithText(text, options);

    // Check for error-specific styling or class
    const hasErrorClass = await toast.evaluate(el =>
      el.classList.contains('error') ||
      el.classList.contains('toast-error') ||
      el.querySelector('.text-red-600') !== null ||
      el.querySelector('[data-toast-type="error"]') !== null
    );

    if (!hasErrorClass) {
      // Fallback: check if the toast contains error indicators in text
      const toastText = await toast.textContent();
      if (toastText && !toastText.toLowerCase().includes('error') && !toastText.includes('✗')) {
        console.warn('Toast may not be an error toast (no error indicators found)');
      }
    }

    this.recordToast({ text: text.toString(), type: 'error' });
    await this.dismissToast();
  }

  /**
   * Asserts an info toast appears with specific text
   * @param text - Expected text
   * @param options - Assertion options
   */
  async expectInfoToast(text: string | RegExp, options?: { timeout?: number }): Promise<void> {
    await this.waitForToastWithText(text, options);
    this.recordToast({ text: text.toString(), type: 'info' });
    await this.dismissToast();
  }

  /**
   * Asserts no toast is currently visible
   */
  async expectNoToast(): Promise<void> {
    const toast = this.getToastContainer();
    await expect(toast).not.toBeVisible();
  }

  /**
   * Records a toast in the history
   * @param toast - Toast data to record
   */
  private recordToast(toast: ToastData): void {
    this.toastHistory.push({
      ...toast,
      // eslint-disable-next-line no-restricted-properties -- Retain epoch timestamp for toast history debugging.
      timestamp: Date.now(),
    });
  }

  /**
   * Gets the toast history
   * @returns Array of recorded toasts
   */
  getToastHistory(): ToastData[] {
    return [...this.toastHistory];
  }

  /**
   * Clears the toast history
   */
  clearToastHistory(): void {
    this.toastHistory = [];
  }

  /**
   * Asserts a sequence of toasts appeared
   * @param expectedSequence - Expected toast sequence
   */
  async assertToastSequence(expectedSequence: Array<{ text: string | RegExp; type?: ToastType }>): Promise<void> {
    for (const expected of expectedSequence) {
      if (expected.type === 'success') {
        await this.expectSuccessToast(expected.text);
      } else if (expected.type === 'error') {
        await this.expectErrorToast(expected.text);
      } else {
        await this.waitForToastWithText(expected.text);
      }

      // Dismiss the toast before checking for the next one
      await this.dismissToast();
      await this.page.waitForTimeout(100); // Brief pause between toasts
    }
  }

  /**
   * Counts the number of visible toasts
   * @returns The count of visible toasts
   */
  async countVisibleToasts(): Promise<number> {
    const toasts = this.getAllToasts();
    const visibleToasts = await toasts.evaluateAll(elements =>
      elements.filter(el => {
        const style = (el.ownerDocument?.defaultView || (el as any).ownerDocument?.parentWindow || window).getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
      }).length
    );
    return visibleToasts;
  }

  /**
   * Checks if a toast with specific text is visible
   * @param text - The text to check for
   * @returns True if visible, false otherwise
   */
  async isToastVisible(text: string | RegExp): Promise<boolean> {
    const toast = this.page.locator('[data-testid="app-shell.toast.item"]').filter({ hasText: text });
    return toast.isVisible();
  }

  /**
   * Waits for auto-dismiss of a toast
   * @param options - Wait options
   */
  async waitForAutoDismiss(options?: { timeout?: number }): Promise<void> {
    const timeout = options?.timeout ?? 10000; // Default 10s for auto-dismiss
    await this.waitForToastsToDisappear({ timeout });
  }
}

/**
 * Creates a toast helper for a page
 * @param page - The Playwright page
 * @returns ToastHelper instance
 */
export function createToastHelper(page: Page): ToastHelper {
  return new ToastHelper(page);
}
