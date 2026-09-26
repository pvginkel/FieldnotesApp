import type { ReactNode } from 'react';

/**
 * DescriptionList Component
 *
 * A reusable container for label-value pair displays. Provides consistent spacing
 * and layout across detail views, cards, and information panels.
 *
 * @example
 * ```tsx
 * <DescriptionList spacing="default">
 *   <DescriptionItem label="Part ID" value={partId} variant="prominent" />
 *   <DescriptionItem label="Manufacturer" value={manufacturer} />
 *   <DescriptionItem label="Created" value={formattedDate} variant="muted" />
 * </DescriptionList>
 * ```
 */

interface DescriptionListProps {
  /** Child DescriptionItem elements */
  children: ReactNode;
  /**
   * Controls vertical spacing between items
   * - compact: space-y-1 (tight grouping)
   * - default: space-y-2 (standard spacing)
   * - relaxed: space-y-4 (section spacing)
   * @default "default"
   */
  spacing?: 'compact' | 'default' | 'relaxed';
  /** Optional data-testid for Playwright selectors */
  testId?: string;
}

export const DescriptionList: React.FC<DescriptionListProps> = ({
  children,
  spacing = 'default',
  testId,
}) => {
  // Map spacing prop to Tailwind classes
  const spacingClass = {
    compact: 'space-y-1',
    default: 'space-y-2',
    relaxed: 'space-y-4',
  }[spacing];

  return (
    <div className={spacingClass} data-testid={testId}>
      {children}
    </div>
  );
};

/**
 * DescriptionItem Component
 *
 * Renders a single label-value pair with semantic styling variants.
 *
 * Value Rendering Rules:
 * 1. If `children` provided, render children (takes precedence over `value`)
 * 2. If `value` provided and non-null/non-undefined, render value
 * 3. If `value` is null/undefined/empty string, render empty div (no placeholder text)
 * 4. Caller is responsible for providing fallback text via `value` prop if desired
 *    Example: value={part.type?.name ?? 'No type assigned'}
 *
 * Rationale: Keeping placeholder logic at call sites preserves existing UX decisions
 * (some fields show "No X assigned", others show "—", others show empty space) and
 * avoids adding string-matching logic to the generic component.
 *
 * @example Basic usage
 * ```tsx
 * <DescriptionItem label="Status" value="Active" />
 * ```
 *
 * @example With variant
 * ```tsx
 * <DescriptionItem
 *   label="Part ID"
 *   value={displayId}
 *   variant="prominent"
 * />
 * ```
 *
 * @example With custom rendering
 * ```tsx
 * <DescriptionItem label="Product Page">
 *   <ExternalLink href={url}>{url}</ExternalLink>
 * </DescriptionItem>
 * ```
 *
 * @example With icon
 * ```tsx
 * <DescriptionItem
 *   icon={<PackageIcon />}
 *   label="Storage Location"
 *   value={location}
 * />
 * ```
 */

interface DescriptionItemProps {
  /** Label text or custom element */
  label: string | ReactNode;
  /** Simple value (string/number) or custom rendering. Overridden by children. */
  value?: string | number | ReactNode;
  /** Alternative to value for complex content. Takes precedence over value. */
  children?: ReactNode;
  /**
   * Visual treatment of the label-value pair
   * - default: label text-sm font-medium, value text-lg
   * - prominent: label text-sm font-medium, value text-2xl font-bold
   * - compact: label text-sm font-medium, value text-sm
   * - muted: label text-sm font-medium, value text-sm text-muted-foreground
   * @default "default"
   */
  variant?: 'default' | 'prominent' | 'compact' | 'muted';
  /** Optional icon before label */
  icon?: ReactNode;
  /** Optional data-testid for the item container */
  testId?: string;
  /** Optional data-testid for label element (rare, specific assertions) */
  labelTestId?: string;
  /** Optional data-testid for value element (rare, specific assertions) */
  valueTestId?: string;
}

export const DescriptionItem: React.FC<DescriptionItemProps> = ({
  label,
  value,
  children,
  variant = 'default',
  icon,
  testId,
  labelTestId,
  valueTestId,
}) => {
  // Variant determines label and value styling
  const labelClass = 'text-sm font-medium';

  const valueClass = {
    default: 'text-lg',
    prominent: 'text-2xl font-bold',
    compact: 'text-sm',
    muted: 'text-sm text-muted-foreground',
  }[variant];

  // Determine what to render in value slot
  // Priority: children > value > empty div
  const valueContent = children ?? value ?? '';

  return (
    <div data-testid={testId}>
      {/* Label with optional icon */}
      <div className={labelClass} data-testid={labelTestId}>
        {icon && <span className="mr-1 inline-block">{icon}</span>}
        {label}
      </div>

      {/* Value slot */}
      <div className={valueClass} data-testid={valueTestId}>
        {valueContent}
      </div>
    </div>
  );
};
