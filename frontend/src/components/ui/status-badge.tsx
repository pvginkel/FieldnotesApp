import * as React from 'react';
import { Badge } from './badge';
import { cn } from '@/lib/utils';

// Bold color palette for entity status badges
const COLOR_CLASSES = {
  inactive: 'bg-slate-400 text-slate-700',
  active: 'bg-blue-600 text-white',
  success: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
  warning: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300'
} as const;

// Size variants for different contexts
const SIZE_CLASSES = {
  default: 'text-xs px-2.5 py-0.5',
  large: 'text-sm px-3 py-1',
} as const;

export interface StatusBadgeProps {
  /** Semantic color (one of 3 bold status colors) */
  color: keyof typeof COLOR_CLASSES;
  /** Status label to display */
  label: string;
  /** Badge size (defaults to default) */
  size?: keyof typeof SIZE_CLASSES;
  /** Test ID for Playwright selectors */
  testId: string;
}

/**
 * StatusBadge — Standardized badge for entity status display
 *
 * Pure presentational component that renders entity status with bold, high-contrast colors.
 * Does NOT know about domain-specific status values — call sites must map domain status
 * (e.g., "concept", "ready", "done") to badge color and label.
 *
 * Intentionally does not support custom className prop to enforce the 3-color abstraction.
 *
 * Color semantics:
 * - inactive: Planning phase, finished work, soft-deleted entities
 * - active: Work in progress, approved for action, current working set
 * - success: Successfully finished tasks
 *
 * @example
 * // Call-site mapping (shopping list status)
 * const { color, label } = status === 'concept'
 *   ? { color: 'inactive' as const, label: 'Concept' }
 *   : status === 'ready'
 *   ? { color: 'active' as const, label: 'Ready' }
 *   : { color: 'inactive' as const, label: 'Completed' };
 *
 * <StatusBadge color={color} label={label} testId="shopping-lists.detail.header.status" />
 */
export const StatusBadge = React.forwardRef<HTMLSpanElement, StatusBadgeProps>(
  ({ color, label, size = 'default', testId }, ref) => {
    const colorClasses = COLOR_CLASSES[color];
    const sizeClasses = SIZE_CLASSES[size];

    return (
      <Badge
        ref={ref}
        variant="outline"
        className={cn(colorClasses, sizeClasses)}
        data-testid={testId}
      >
        {label}
      </Badge>
    );
  }
);

StatusBadge.displayName = 'StatusBadge';
