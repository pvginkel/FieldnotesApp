/**
 * Factory for test authentication endpoints.
 * These endpoints are only available when the backend runs in test mode.
 *
 * Uses Playwright's Page to make requests through the browser context,
 * ensuring cookies are shared with subsequent page navigations.
 */

import { randomUUID } from 'node:crypto';
import type { APIResponse, Page } from '@playwright/test';

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
      throw await requestError('Failed to create test session', response);
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
      throw await requestError('Failed to clear test session', response);
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
      throw await requestError('Failed to configure auth error', response);
    }
  }
}

/** An error naming the failed request, with the backend's response body. */
async function requestError(what: string, response: APIResponse): Promise<Error> {
  let body = '';
  try {
    body = await response.text();
  } catch {
    // The body is a diagnostic; the status still says what failed.
  }
  return new Error(`${what}: ${response.status()} ${response.statusText()}\n${body}`);
}
