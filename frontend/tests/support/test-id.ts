/**
 * Builds a data-testid CSS selector.
 * Standalone module so both selectors.ts and selectors-domain.ts can
 * import it without creating a circular dependency.
 */
export function testId(id: string): string {
  return `[data-testid="${id}"]`;
}
