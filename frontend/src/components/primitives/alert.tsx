import * as React from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { alertVariantClasses, alertDefaultIcons } from '@/styles/alert';

/**
 * Alert component variant types
 */
export type AlertVariant = 'error' | 'warning' | 'info' | 'success';

/**
 * Alert component props
 */
export interface AlertProps {
  /**
   * Visual variant determining color scheme and default icon
   * - error: Destructive red/pink styling with AlertCircle icon
   * - warning: Amber/yellow styling with AlertTriangle icon
   * - info: Blue styling with Info icon
   * - success: Green styling with CheckCircle2 icon
   */
  variant: AlertVariant;

  /**
   * Alert content (text or React nodes)
   */
  children: React.ReactNode;

  /**
   * Optional icon to display before content
   * - true: Render default icon for variant
   * - ReactNode: Render custom icon
   * - false/undefined: No icon
   */
  icon?: React.ReactNode | boolean;

  /**
   * Optional bold heading displayed above body content
   */
  title?: string;

  /**
   * Optional dismiss handler
   * When provided, renders a dismiss button with testId="${testId}.dismiss"
   * Parent must update visibility state to hide the Alert when callback invoked
   */
  onDismiss?: () => void;

  /**
   * Optional action button(s)
   * Parent provides styled Button components with testIds
   * Rendered in flex container alongside dismiss button (if both present)
   */
  action?: React.ReactNode;

  /**
   * Optional layout classes (margins, width, positioning, z-index) merged via cn()
   * DO NOT pass styling classes (colors, borders, padding) - these are controlled by variant
   */
  className?: string;

  /**
   * Required base testId for Playwright targeting
   * Applied to container div; dismiss button automatically gets "${testId}.dismiss"
   */
  testId: string;
}

/**
 * Alert — Standardized component for contextual error, warning, info, and success messages
 *
 * Pure presentational component for displaying inline alert banners with optional icons,
 * titles, dismissible behavior, and action buttons. Visibility is controlled by parent
 * via conditional rendering.
 *
 * Variant-to-style mapping:
 * - error: border-destructive/50 bg-destructive/10 text-destructive
 * - warning: border-amber-300 bg-amber-50 text-amber-900 (light) / border-amber-500/50 bg-amber-500/10 text-amber-400 (dark)
 * - info: border-blue-300 bg-blue-50 text-blue-900
 * - success: border-green-300 bg-green-50 text-green-900
 *
 * Standardized spacing: px-4 py-3 padding, gap-3 icon spacing, rounded-md borders
 *
 * @example
 * // Error alert with retry action
 * <Alert
 *   variant="error"
 *   icon={true}
 *   testId="parts.detail.error"
 *   action={
 *     <Button onClick={retry} size="sm" data-testid="parts.detail.error.retry">
 *       Retry
 *     </Button>
 *   }
 * >
 *   Failed to load part details.
 * </Alert>
 *
 * @example
 * // Warning alert with title and dismiss
 * <Alert
 *   variant="warning"
 *   icon={true}
 *   title="Duplicate part detected"
 *   onDismiss={() => setError(null)}
 *   testId="shopping-lists.concept.duplicate-banner"
 * >
 *   Part {partKey} already exists in this list.
 * </Alert>
 *
 * @example
 * // Info alert with custom icon and className for layout
 * <Alert
 *   variant="info"
 *   icon={<CustomIcon />}
 *   className="mb-4 w-full"
 *   testId="app.notification"
 * >
 *   A new version is available.
 * </Alert>
 */
export const Alert = React.forwardRef<HTMLDivElement, AlertProps>(
  ({ variant, children, icon, title, onDismiss, action, className, testId }, ref) => {
    // Determine which icon to render
    let iconNode: React.ReactNode = null;
    if (icon === true) {
      const IconComponent = alertDefaultIcons[variant];
      iconNode = <IconComponent className="h-5 w-5 flex-shrink-0" />;
    } else if (icon && typeof icon !== 'boolean') {
      iconNode = icon;
    }

    // Determine accessible role based on variant severity
    const role = variant === 'error' || variant === 'warning' ? 'alert' : 'status';

    // Auto-generate aria-label for dismiss button
    const dismissLabel = title ? `Close ${title} alert` : 'Close alert';

    return (
      <div
        ref={ref}
        role={role}
        className={cn(
          'flex gap-3 rounded-md border px-4 py-3 text-sm',
          alertVariantClasses[variant],
          className
        )}
        data-testid={testId}
      >
        {/* Icon slot */}
        {iconNode && <div className="flex-shrink-0">{iconNode}</div>}

        {/* Content slot */}
        <div className="flex-1 space-y-1">
          {title && <div className="font-semibold">{title}</div>}
          <div>{children}</div>
        </div>

        {/* Action and dismiss buttons */}
        {(action || onDismiss) && (
          <div className="flex items-center gap-2">
            {action}
            {onDismiss && (
              <button
                type="button"
                onClick={onDismiss}
                className="flex-shrink-0 cursor-pointer rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2"
                aria-label={dismissLabel}
                data-testid={`${testId}.dismiss`}
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        )}
      </div>
    );
  }
);

Alert.displayName = 'Alert';
