/**
 * Domain-specific test fixtures.
 * App-owned — extends infrastructure fixtures with domain page objects.
 *
 * `store` lays out the worker backend's store for the test, `controller` its KubeCoder controller;
 * `triage` and `inbox` are the two screens, `appBar` their headers' app bar.
 */

/* eslint-disable react-hooks/rules-of-hooks */
import { Controller } from './controller';
import { infrastructureFixtures } from './fixtures-infrastructure';
import { AppBar } from './page-objects/app-bar';
import { InboxPage } from './page-objects/inbox-page';
import { TriagePage } from './page-objects/triage-page';
import { TriageStore } from './triage-store';

interface DomainFixtures {
  store: TriageStore;
  controller: Controller;
  triage: TriagePage;
  inbox: InboxPage;
  appBar: AppBar;
}

export const test = infrastructureFixtures.extend<DomainFixtures>({
  store: async ({ playwright, backendUrl }, use) => {
    const request = await playwright.request.newContext();
    try {
      await use(new TriageStore(request, backendUrl));
    } finally {
      await request.dispose();
    }
  },
  controller: async ({ playwright, backendUrl }, use) => {
    const request = await playwright.request.newContext();
    try {
      await use(new Controller(request, backendUrl));
    } finally {
      await request.dispose();
    }
  },
  triage: async ({ page, frontendUrl }, use) => {
    await use(new TriagePage(page, frontendUrl));
  },
  inbox: async ({ page, frontendUrl }, use) => {
    await use(new InboxPage(page, frontendUrl));
  },
  appBar: async ({ page }, use) => {
    await use(new AppBar(page));
  },
});

export type { InfrastructureFixtures } from './fixtures-infrastructure';
export { expect } from '@playwright/test';
