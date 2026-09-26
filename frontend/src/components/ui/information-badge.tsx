import * as React from 'react';
import { cn } from '@/lib/utils';

// Visual variants for information badges
const VARIANT_CLASSES = {
  default: 'bg-secondary text-secondary-foreground rounded-md',
  subtle: 'text-muted-foreground',
} as const;

export interface InformationBadgeProps {
  /** Optional icon/emoji to display before content */
  icon?: string;
  /** Badge content (text or React nodes) */
  children: React.ReactNode;
  /** Optional remove handler for removable tags. When provided, renders an accessible remove button with aria-label */
  onRemove?: () => void;
  /** Visual variant (defaults to 'default') */
  variant?: keyof typeof VARIANT_CLASSES;
  /** Test ID for Playwright selectors - REQUIRED for test reliability */
  testId: string;
}

/**
 * InformationBadge — Standardized badge for displaying metadata, tags, and supplementary information
 *
 * Pure presentational component that renders supplementary information with optional icon and remove functionality.
 * Implemented as a standalone span element (not wrapping base Badge component) to allow explicit border-radius control.
 * The base Badge component hardcodes `rounded-full`, but InformationBadge needs `rounded-md` for tags.
 *
 * Intentionally does not support custom className prop to enforce style encapsulation.
 *
 * Variant semantics:
 * - default: Secondary background with rounded corners (for tags and metadata badges)
 * - subtle: Muted foreground without background (for inline information displays)
 *
 * @example
 * // Tag with remove functionality
 * <InformationBadge
 *   onRemove={() => removeTag(index)}
 *   testId={`parts.form.tag.${index}`}
 * >
 *   {tagName}
 * </InformationBadge>
 *
 * @example
 * // Metadata badge with icon
 * <InformationBadge
 *   icon="📦"
 *   testId="parts.detail.badge.package"
 * >
 *   SOT-23
 * </InformationBadge>
 *
 * @example
 * // Subtle variant for location summary
 * <InformationBadge
 *   icon="📊"
 *   variant="subtle"
 *   testId="parts.detail.location-summary"
 * >
 *   3 locations, 150 units
 * </InformationBadge>
 */
export const InformationBadge = React.forwardRef<HTMLSpanElement, InformationBadgeProps>(
  ({ icon, children, onRemove, variant = 'default', testId }, ref) => {
    const variantClasses = VARIANT_CLASSES[variant];

    // Extract text content for aria-label (handle both string and ReactNode children)
    const childrenText = typeof children === 'string'
      ? children
      : React.Children.toArray(children)
          .filter((child): child is string | number => typeof child === 'string' || typeof child === 'number')
          .join('');

    return (
      <span
        ref={ref}
        className={cn(
          'inline-flex items-center gap-1 px-2 py-1 text-xs',
          variantClasses
        )}
        data-testid={testId}
      >
        {icon && <span className="text-xs">{icon}</span>}
        <span>{children}</span>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="ml-0.5 cursor-pointer text-xs hover:opacity-70"
            aria-label={`Remove ${childrenText}`}
          >
            ×
          </button>
        )}
      </span>
    );
  }
);

InformationBadge.displayName = 'InformationBadge';
