/**
 * Factory for test authentication endpoints.
 * These endpoints are only available when the backend runs in test mode.
 *
 * Uses Playwright's Page to make requests through the browser context,
 * ensuring cookies are shared with subsequent page navigations.
 */

import { randomUUID } from 'node:crypto';
import type { Page } from '@playwright/test';

export interface TestUserOptions {
  subject?: string;
  name?: string | null;
  email?: string | null;
  roles?: string[];
}

export class AuthFactory {
  constructor(
    private baseUrl: string,
    private page: Page,
  ) {}

  /**
   * Create an authenticated session with controllable user fields.
   * Sets the session cookie that /api/auth/self will recognize.
   */
  async createSession(options: TestUserOptions = {}): Promise<void> {
    const response = await this.page.request.post(
      `${this.baseUrl}/api/testing/auth/session`,
      {
        data: {
          subject: options.subject ?? `test-user-${randomUUID()}`,
          name: options.name,
          email: options.email,
          roles: options.roles ?? [],
        },
      },
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to create test session: ${response.status()} ${response.statusText()}`,
      );
    }
  }

  /**
   * Clear the current session for test isolation.
   * Clears both backend session and browser cookies.
   */
  async clearSession(): Promise<void> {
    const response = await this.page.request.post(
      `${this.baseUrl}/api/testing/auth/clear`,
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to clear test session: ${response.status()} ${response.statusText()}`,
      );
    }

    await this.page.context().clearCookies();
  }

  /**
   * Force /api/auth/self to return a specific error status on the next request.
   * The error is single-shot — subsequent requests resume normal behavior.
   */
  async forceError(status: number): Promise<void> {
    const response = await this.page.request.post(
      `${this.baseUrl}/api/testing/auth/force-error?status=${status}`,
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to configure auth error: ${response.status()} ${response.statusText()}`,
      );
    }
  }
}
