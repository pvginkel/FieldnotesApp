import * as React from 'react';
import { cn } from '@/lib/utils';
import { notificationVariantClasses, notificationDefaultIcons } from '@/styles/notification';

/**
 * InlineNotification component variant types
 */
export type InlineNotificationVariant = 'error' | 'warning' | 'info' | 'success';

/**
 * InlineNotification component props
 */
export interface InlineNotificationProps {
  /**
   * Visual variant determining color scheme and default icon
   * - error: Destructive red/pink styling with AlertCircle icon
   * - warning: Amber/yellow styling with AlertTriangle icon
   * - info: Blue styling with Info icon
   * - success: Green styling with CheckCircle2 icon
   */
  variant: InlineNotificationVariant;

  /**
   * Notification content (text or React nodes)
   */
  children: React.ReactNode;

  /**
   * Optional icon override
   * - true: Render default icon for variant
   * - ReactNode: Render custom icon
   * - false/undefined: No icon
   */
  icon?: React.ReactNode | boolean;

  /**
   * Required base testId for Playwright targeting
   * Applied to container span element
   */
  testId: string;
}

/**
 * InlineNotification — Standardized component for compact inline notifications
 *
 * Pure presentational component for displaying inline warning, error, info, and success
 * notifications that appear inline with content (not block-level like Alert). Designed
 * for table cells, status displays, and compact inline contexts.
 *
 * Variant-to-style mapping:
 * - error: border-destructive/50 bg-destructive/10 text-destructive
 * - warning: border-amber-400 bg-amber-50 text-amber-900
 * - info: border-blue-400 bg-blue-50 text-blue-900
 * - success: border-green-400 bg-green-50 text-green-900
 *
 * Standardized spacing: px-2 py-1 padding, gap-2 icon spacing, rounded borders
 * Icon size: h-3.5 w-3.5 (smaller than Alert's h-5 w-5)
 *
 * NOTE: No className prop is provided—this component enforces consistent styling
 * without layout escape hatches. If additional layout control is needed, wrap the
 * component in a container element.
 *
 * @example
 * // Warning notification with default icon
 * <InlineNotification
 *   variant="warning"
 *   icon={true}
 *   testId="pick-lists.detail.line.123.shortfall"
 * >
 *   Shortfall 6
 * </InlineNotification>
 *
 * @example
 * // Error notification without icon
 * <InlineNotification
 *   variant="error"
 *   testId="parts.stock.error"
 * >
 *   Invalid quantity
 * </InlineNotification>
 *
 * @example
 * // Success notification with custom icon
 * <InlineNotification
 *   variant="success"
 *   icon={<CustomCheckIcon />}
 *   testId="order.status"
 * >
 *   Order complete
 * </InlineNotification>
 */
export const InlineNotification = React.forwardRef<HTMLSpanElement, InlineNotificationProps>(
  ({ variant, children, icon, testId }, ref) => {
    // Determine which icon to render
    let iconNode: React.ReactNode = null;
    if (icon === true) {
      const IconComponent = notificationDefaultIcons[variant];
      iconNode = <IconComponent className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />;
    } else if (icon && typeof icon !== 'boolean') {
      iconNode = icon;
    }

    return (
      <span
        ref={ref}
        className={cn('inline-flex items-center gap-2 rounded border px-2 py-1 text-sm', notificationVariantClasses[variant])}
        data-testid={testId}
      >
        {iconNode}
        {children}
      </span>
    );
  }
);

InlineNotification.displayName = 'InlineNotification';
