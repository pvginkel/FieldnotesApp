import type { MouseEvent, ReactNode } from 'react';
import { Link } from '@tanstack/react-router';
import { Unlink } from 'lucide-react';

import { StatusBadge } from '@/components/ui';
import { Button } from './button';
import { Tooltip } from './tooltip';
import { cn } from '@/lib/utils';

export interface LinkChipProps {
  // Navigation
  to: string;
  openInNewTab?: boolean;
  params: Record<string, string>;
  search?: Record<string, unknown>;

  // Content
  label: string;
  icon?: ReactNode;
  statusBadgeColor: 'active' | 'inactive' | 'success' | 'warning';
  statusBadgeLabel: string;

  // Accessibility
  accessibilityLabel: string;

  // Test IDs
  testId?: string;
  wrapperTestId?: string;
  iconTestId?: string;
  badgeTestId?: string;

  // Info icon (optional - shows tooltip on hover)
  infoIcon?: ReactNode;
  infoTooltip?: ReactNode;
  infoIconTestId?: string;

  // Unlink behavior
  onUnlink?: () => void;
  unlinkDisabled?: boolean;
  unlinkLoading?: boolean;
  unlinkTestId?: string;
  unlinkTooltip?: string;
  unlinkLabel?: string;
}

/**
 * LinkChip — Shared link chip pattern for navigating to domain entities
 *
 * Pure presentational component that renders a rounded pill-shaped chip with:
 * - TanStack Router Link navigation
 * - Icon, label, and status badge
 * - Optional unlink button that appears on hover/focus
 * - Comprehensive accessibility patterns
 *
 * Intentionally does not support className prop to enforce visual consistency.
 * Domain-specific wrappers (KitLinkChip, ShoppingListLinkChip) map domain types
 * to this interface.
 *
 * @example
 * // Used via domain wrapper
 * <KitLinkChip kitId={123} name="My Kit" status="active" />
 *
 * // Direct usage (not typical)
 * <LinkChip
 *   to="/kits/$kitId"
 *   params={{ kitId: "123" }}
 *   label="My Kit"
 *   statusBadgeColor="active"
 *   statusBadgeLabel="Active"
 *   accessibilityLabel="My Kit (Active)"
 *   testId="example.chip"
 * />
 */
export function LinkChip({
  to,
  openInNewTab,
  params,
  search,
  label,
  icon,
  statusBadgeColor,
  statusBadgeLabel,
  accessibilityLabel,
  testId,
  wrapperTestId,
  iconTestId,
  badgeTestId,
  infoIcon,
  infoTooltip,
  infoIconTestId,
  onUnlink,
  unlinkDisabled,
  unlinkLoading,
  unlinkTestId,
  unlinkTooltip,
  unlinkLabel = 'Unlink',
}: LinkChipProps) {
  // Guidepost: Compute derived testId for wrapper using .wrapper suffix pattern
  const resolvedWrapperTestId = wrapperTestId ?? (testId ? `${testId}.wrapper` : undefined);

  // Guidepost: Handle unlink click — prevent propagation and navigation
  const handleUnlinkClick = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    onUnlink?.();
  };

  // Guidepost: Handle info icon click — prevent propagation to avoid triggering navigation
  const handleInfoIconClick = (event: MouseEvent<HTMLSpanElement>) => {
    event.stopPropagation();
  };

  return (
    <div
      className={cn(
        'group relative inline-flex items-center gap-2 rounded-full border border-input bg-muted px-2 py-1 text-sm transition-all hover:border-primary',
        // Guidepost: Conditionally expand padding on hover/focus to accommodate unlink button
        onUnlink && 'hover:pr-9 focus-within:pr-9 [@media(pointer:coarse)]:pr-9',
      )}
      data-testid={resolvedWrapperTestId}
      aria-label={accessibilityLabel}
    >
      <Link
        to={to as any}
        target={openInNewTab ? "_blank" : undefined}
        params={params as any}
        search={search as any}
        className="flex min-w-0 flex-1 items-center gap-2 rounded-full px-1 py-0.5 transition-colors hover:text-primary focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary"
        data-testid={testId}
        aria-label={accessibilityLabel}
        onClick={(event) => event.stopPropagation()}
      >
        <span className="flex items-center gap-2 min-w-0">
          {icon && (
            <span
              className="flex-shrink-0 text-muted-foreground transition-colors group-hover:text-primary"
              data-testid={iconTestId}
              aria-hidden="true"
            >
              {icon}
            </span>
          )}
          <span className="truncate">{label}</span>
        </span>
        <StatusBadge
          color={statusBadgeColor}
          label={statusBadgeLabel}
          size="default"
          testId={badgeTestId ?? ''}
        />
      </Link>
      {/* Guidepost: Info icon renders OUTSIDE Link element to prevent navigation on hover/click */}
      {infoIcon && infoTooltip && (
        <Tooltip content={infoTooltip} placement="auto" testId={infoIconTestId}>
          <span
            className="flex-shrink-0 text-muted-foreground hover:text-primary transition-colors cursor-help"
            onClick={handleInfoIconClick}
            data-testid={infoIconTestId}
          >
            {infoIcon}
          </span>
        </Tooltip>
      )}
      {onUnlink ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={cn(
            'absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6 rounded-full p-0 text-muted-foreground hover:text-destructive focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary',
            'opacity-0 transition-opacity duration-200 group-hover:opacity-100 group-focus-within:opacity-100 [@media(pointer:coarse)]:opacity-100',
            '@media(prefers-reduced-motion:reduce):transition-none',
          )}
          onClick={handleUnlinkClick}
          disabled={unlinkDisabled}
          loading={unlinkLoading}
          data-testid={unlinkTestId}
          title={unlinkTooltip ?? unlinkLabel}
          aria-label={unlinkLabel}
        >
          <Unlink className="h-4 w-4" aria-hidden="true" />
        </Button>
      ) : null}
    </div>
  );
}
