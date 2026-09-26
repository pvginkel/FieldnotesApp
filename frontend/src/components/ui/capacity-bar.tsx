import * as React from 'react';
import { ProgressBar } from '@/components/primitives/progress-bar';

export interface CapacityBarProps {
  /** Number of used/occupied units (e.g., occupied locations). Must be non-negative. */
  used: number;
  /** Total capacity/available units (e.g., total locations). Must be non-negative. */
  total: number;
  /** Optional label text (defaults to "Usage") */
  label?: string;
  /** Optional test ID for the root element */
  testId?: string;
  // Note: className intentionally NOT supported to enforce consistent styling.
  // ProgressBar is composed internally with fixed size="md" and variant="default"
  // without any prop spreading or customization.
}

/**
 * CapacityBar — High-level component displaying resource usage with text and visual progress bar
 *
 * Pure presentational component that combines a textual usage label with a visual progress bar
 * to show capacity utilization. Commonly used in detail views and cards to display storage
 * capacity, quota usage, or other resource metrics.
 *
 * Intentionally does not support custom className prop to enforce consistent capacity styling.
 * Layout adjustments (margin, positioning) should be handled by parent containers.
 *
 * Edge Cases:
 * - Division by zero: If total <= 0, renders 0% to avoid NaN
 * - Over-capacity: If used > total, percentage is clamped to 100%
 * - Negative values: Assumes consumers provide valid non-negative integers
 *
 * @example
 * // Box storage capacity
 * <CapacityBar
 *   used={box.occupied_locations ?? 0}
 *   total={box.total_locations ?? box.capacity}
 * />
 *
 * @example
 * // Custom label
 * <CapacityBar
 *   used={5}
 *   total={10}
 *   label="Storage"
 *   testId="my-capacity-bar"
 * />
 */
export const CapacityBar = React.forwardRef<HTMLDivElement, CapacityBarProps>(
  ({ used, total, label = 'Usage', testId }, ref) => {
    // Clamp to non-negative values to enforce documented requirements
    const safeUsed = Math.max(0, used);
    const safeTotal = Math.max(0, total);

    // Validate finite numbers and guard against division by zero
    const percentage =
      Number.isFinite(safeUsed) &&
      Number.isFinite(safeTotal) &&
      safeTotal > 0
        ? Math.min(100, Math.round((safeUsed / safeTotal) * 100))
        : 0;

    return (
      <div ref={ref} data-testid={testId}>
        <div className="text-sm text-muted-foreground">
          {label}: {safeUsed}/{safeTotal} ({percentage}%)
        </div>
        <div className="mt-2">
          <ProgressBar value={percentage} size="md" variant="default" />
        </div>
      </div>
    );
  }
);

CapacityBar.displayName = 'CapacityBar';
