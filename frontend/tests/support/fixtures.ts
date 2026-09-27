/**
 * Domain-specific test fixtures.
 * App-owned — extends infrastructure fixtures with domain page objects.
 *
 * `store` lays out the worker backend's store for the test; `triage` is the triage screen.
 */

/* eslint-disable react-hooks/rules-of-hooks */
import { infrastructureFixtures } from './fixtures-infrastructure';
import { TriagePage } from './page-objects/triage-page';
import { TriageStore } from './triage-store';

export const test = infrastructureFixtures.extend<{ store: TriageStore; triage: TriagePage }>({
  store: async ({ playwright, backendUrl }, use) => {
    const request = await playwright.request.newContext();
    try {
      await use(new TriageStore(request, backendUrl));
    } finally {
      await request.dispose();
    }
  },
  triage: async ({ page, frontendUrl }, use) => {
    await use(new TriagePage(page, frontendUrl));
  },
});

export type { InfrastructureFixtures } from './fixtures-infrastructure';
export { expect } from '@playwright/test';
