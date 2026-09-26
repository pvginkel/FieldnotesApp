/**
 * Domain-specific test fixtures.
 * App-owned — extends infrastructure fixtures with domain page objects.
 *
 * Example:
 *   import { test as infrastructureTest } from './fixtures-infrastructure';
 *
 *   export const test = infrastructureTest.extend<{ myPage: MyPageObject }>({
 *     myPage: async ({ page }, use) => {
 *       await use(new MyPageObject(page));
 *     },
 *   });
 *
 *   export { expect } from '@playwright/test';
 */

/* eslint-disable react-hooks/rules-of-hooks */
export { infrastructureFixtures as test } from './fixtures-infrastructure';
export type { InfrastructureFixtures } from './fixtures-infrastructure';
export { expect } from '@playwright/test';
