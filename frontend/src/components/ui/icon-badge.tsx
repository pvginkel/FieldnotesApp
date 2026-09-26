import * as React from 'react';
import { cn } from '@/lib/utils';

// Size variants for different contexts
const SIZE_CLASSES = {
  sm: 'w-8 h-8 text-sm',
  md: 'w-10 h-10 text-base',
  lg: 'w-12 h-12 text-lg',
  xl: 'w-16 h-16 text-xl',
} as const;

// Color variants for semantic status indicators
const VARIANT_CLASSES = {
  success: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-800 dark:text-emerald-300',
  error: 'bg-amber-50 text-amber-600 dark:bg-amber-800 dark:text-amber-300',
  warning: 'bg-amber-50 text-amber-600 dark:bg-amber-800 dark:text-amber-300',
  info: 'bg-blue-50 text-blue-600 dark:bg-blue-800 dark:text-blue-200', /* Changed from blue-300 to blue-200 for WCAG AA compliance */
  neutral: 'bg-muted text-muted-foreground',
  primary: 'bg-primary text-primary-foreground',
  destructive: 'bg-destructive/10 text-destructive',
} as const;

// Border colors matching each variant
const BORDER_CLASSES = {
  success: 'border-emerald-200 dark:border-emerald-700',
  error: 'border-amber-200 dark:border-amber-700',
  warning: 'border-amber-200 dark:border-amber-700',
  info: 'border-blue-200 dark:border-blue-700',
  neutral: 'border-muted',
  primary: 'border-primary',
  destructive: 'border-destructive',
} as const;

export interface IconBadgeProps {
  /** Badge content (text, emoji, or React node including Lucide icons) */
  children: React.ReactNode;
  /**
   * Optional test ID for the container.
   *
   * Provide testId when the badge is independently tested and needs direct test targeting
   * (e.g., activity icons, milestone badges that are specifically asserted in tests).
   *
   * Omit testId when the parent container provides sufficient test targeting
   * (e.g., location item badges where tests target the parent location container).
   */
  testId?: string;
  /** Size variant (defaults to 'sm') */
  size?: keyof typeof SIZE_CLASSES;
  /** Color scheme variant (defaults to 'neutral') */
  variant?: keyof typeof VARIANT_CLASSES;
  /** Add border (defaults to false) */
  border?: boolean;
  /** Apply pulse animation (defaults to false) */
  animated?: boolean;
  /** Click handler for interactive badges */
  onClick?: () => void;
}

/**
 * IconBadge — Standardized circular badge for icons, status indicators, and activity markers
 *
 * Pure presentational component that renders circular badges with semantic color schemes.
 * Does NOT know about domain-specific states — call sites must map domain logic
 * (e.g., location occupancy, activity type, achievement status) to badge variant.
 *
 * Intentionally does not support custom className prop to enforce strict style encapsulation.
 * Layout adjustments (margin, positioning) should be handled by parent containers.
 *
 * Variant semantics:
 * - success: Positive states (occupied locations, achieved milestones, additions)
 * - error/warning: Alert states (activity removals, warnings)
 * - info: Informational states (activity moves, updates)
 * - neutral: Default/empty states (unoccupied locations, future milestones)
 * - primary: Highlighted states (step numbers, next milestone targets)
 * - destructive: Error states (AI failures, image load errors)
 *
 * Size variants:
 * - sm (w-8 h-8): Default for most badges (location numbers, activity icons, milestones)
 * - md (w-10 h-10): Medium badges (action buttons)
 * - lg (w-12 h-12): Large badges
 * - xl (w-16 h-16): Extra large badges (AI progress states, error displays)
 *
 * Icon size guidelines:
 * - sm badge: h-4 w-4 icon
 * - md badge: h-5 w-5 icon
 * - lg badge: h-6 w-6 icon
 * - xl badge: h-8 w-8 icon
 *
 * @example
 * // Location number badge with conditional variant
 * <IconBadge
 *   size="sm"
 *   variant={location.isOccupied ? 'success' : 'neutral'}
 * >
 *   {location.locNo}
 * </IconBadge>
 *
 * @example
 * // Activity timeline badge with border and icon
 * <IconBadge
 *   size="sm"
 *   variant="success"
 *   border
 *   testId="dashboard.activity.item.icon"
 * >
 *   ➕
 * </IconBadge>
 *
 * @example
 * // Milestone badge with animation
 * <IconBadge
 *   size="sm"
 *   variant="primary"
 *   animated
 * >
 *   50%
 * </IconBadge>
 *
 * @example
 * // AI progress error badge with Lucide icon
 * <IconBadge
 *   size="xl"
 *   variant="destructive"
 * >
 *   <X className="h-8 w-8" />
 * </IconBadge>
 */
export const IconBadge = React.forwardRef<
  HTMLDivElement | HTMLButtonElement,
  IconBadgeProps
>(
  (
    {
      children,
      testId,
      size = 'sm',
      variant = 'neutral',
      border = false,
      animated = false,
      onClick,
    },
    ref
  ) => {
    const sizeClasses = SIZE_CLASSES[size];
    const variantClasses = VARIANT_CLASSES[variant];
    const borderClasses = border ? `border-2 ${BORDER_CLASSES[variant]}` : '';
    const animationClasses = animated
      ? 'animate-pulse transition-all duration-300 motion-reduce:animate-none'
      : '';
    const interactiveClasses = onClick
      ? 'cursor-pointer hover:opacity-80 transition-opacity'
      : '';

    const className = cn(
      'inline-flex items-center justify-center rounded-full font-medium',
      sizeClasses,
      variantClasses,
      borderClasses,
      animationClasses,
      interactiveClasses
    );

    const commonProps = {
      className,
      ...(testId ? { 'data-testid': testId } : {}),
    };

    // Render as button when onClick is provided for semantic HTML and accessibility
    if (onClick) {
      return (
        <button
          ref={ref as React.Ref<HTMLButtonElement>}
          type="button"
          onClick={onClick}
          {...commonProps}
        >
          {children}
        </button>
      );
    }

    // Render as div for static badges
    return (
      <div ref={ref as React.Ref<HTMLDivElement>} {...commonProps}>
        {children}
      </div>
    );
  }
);

IconBadge.displayName = 'IconBadge';
