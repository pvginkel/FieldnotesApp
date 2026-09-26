import * as React from 'react';

/**
 * SectionHeading component props
 */
export interface SectionHeadingProps {
  /**
   * Visual variant determining size and spacing
   * - subsection: mb-2 text-xs (default, for subsection labels)
   * - section: mb-3 text-sm (for higher-level section labels)
   */
  variant?: 'subsection' | 'section';

  /**
   * Heading text content
   */
  children: React.ReactNode;

  /**
   * Optional testId for Playwright targeting
   */
  testId?: string;
}

/**
 * SectionHeading — Standardized component for section and subsection labels
 *
 * Pure presentational component for displaying section headings that introduce
 * content sections below them. Provides two variants for different hierarchy levels:
 * - subsection: Smaller heading (text-xs) for subsections within a larger section
 * - section: Larger heading (text-sm) for primary section labels
 *
 * Variant-to-style mapping:
 * - subsection: mb-2 text-xs font-medium text-muted-foreground (default)
 * - section: mb-3 text-sm font-medium
 *
 * NOTE: No className prop is provided—this component enforces consistent styling
 * without layout escape hatches. All current usages are static text labels introducing
 * content sections (manufacturer details, technical specs, tooltip sections, etc.).
 *
 * Design Principles:
 * - NO className prop - all styling is encapsulated
 * - Variant-based styling with clear semantic meaning
 * - testId prop for Playwright targeting when needed
 * - React.forwardRef pattern for ref forwarding
 * - Comprehensive JSDoc documentation
 *
 * @example
 * // Subsection heading (default)
 * <SectionHeading>Manufacturer Information</SectionHeading>
 *
 * @example
 * // Section heading
 * <SectionHeading variant="section">Technical Specifications</SectionHeading>
 *
 * @example
 * // With testId for testing
 * <SectionHeading
 *   variant="subsection"
 *   testId="parts.detail.heading.manufacturer"
 * >
 *   Physical
 * </SectionHeading>
 */
export const SectionHeading = React.forwardRef<HTMLDivElement, SectionHeadingProps>(
  ({ variant = 'subsection', children, testId }, ref) => {
    // Map variant to Tailwind class string
    const variantClasses = {
      subsection: 'mb-2 text-xs font-medium text-muted-foreground',
      section: 'mb-3 text-sm font-medium',
    };

    return (
      <div ref={ref} className={variantClasses[variant]} data-testid={testId}>
        {children}
      </div>
    );
  }
);

SectionHeading.displayName = 'SectionHeading';
