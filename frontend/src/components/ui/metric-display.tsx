import * as React from 'react';
import { cn } from '@/lib/utils';

// Value color variants for conditional styling
const VALUE_COLOR_CLASSES = {
  default: 'text-foreground',
  warning: 'text-amber-600',
} as const;

export interface MetricDisplayProps {
  /** Metric label displayed above value (e.g., "Needed", "Ordered") */
  label: string;
  /** Metric value (number or string) */
  value: string | number;
  /** Optional color variant for value (default: 'default') */
  valueColor?: keyof typeof VALUE_COLOR_CLASSES;
  /** Required test ID applied to value element for Playwright selectors */
  testId: string;
}

/**
 * MetricDisplay — Standardized vertical metric display with label and value
 *
 * Pure presentational component that renders a metric with a label on top and value below
 * in a right-aligned stacked layout. Commonly used in cards and detail views to show
 * statistics, counts, or measurements.
 *
 * Intentionally does not support custom className prop to enforce consistent metric styling.
 * Layout adjustments (margin, positioning) should be handled by parent containers.
 *
 * @example
 * // Default metric (standard foreground color)
 * <MetricDisplay
 *   label="Needed"
 *   value={42}
 *   testId="shopping-lists.ready.group.abc.totals.needed"
 * />
 *
 * @example
 * // Warning metric (amber color for quantity mismatches)
 * <MetricDisplay
 *   label="Received"
 *   value={line.received}
 *   valueColor={line.hasQuantityMismatch ? 'warning' : 'default'}
 *   testId="shopping-lists.ready.update-stock.line.metric.received"
 * />
 */
export const MetricDisplay = React.forwardRef<HTMLDivElement, MetricDisplayProps>(
  ({ label, value, valueColor = 'default', testId }, ref) => {
    const valueColorClasses = VALUE_COLOR_CLASSES[valueColor];

    return (
      <div ref={ref} className="flex flex-col text-right">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <span className={cn('font-semibold', valueColorClasses)} data-testid={testId}>
          {value}
        </span>
      </div>
    );
  }
);

MetricDisplay.displayName = 'MetricDisplay';
