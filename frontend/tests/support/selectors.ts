/**
 * Generic test selector helpers.
 * Template-owned — provides testId(), buildSelector(), and common UI selectors.
 *
 * Domain-specific selectors live in selectors-domain.ts (app-owned).
 */

import { testId } from './test-id';

export { testId };

/**
 * Common UI selectors (generic infrastructure).
 */
export const selectors = {
  common: {
    loading: testId('loading'),
    error: testId('error'),
    toast: testId('toast'),
    search: testId('search'),
    pagination: {
      container: testId('pagination'),
      prev: testId('pagination.prev'),
      next: testId('pagination.next'),
      page: (num: number) => testId(`pagination.page.${num}`),
    },
  },
};

/**
 * Helper to build custom selectors following the pattern.
 */
export function buildSelector(domain: string, section: string, element: string, id?: string): string {
  const parts = [domain, section, element];
  if (id) parts.push(id);
  return testId(parts.join('.'));
}
