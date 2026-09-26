import * as React from 'react';
import { Link } from '@tanstack/react-router';
import { cn } from '@/lib/utils';

interface CardLinkProps {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, unknown>;
  disabled?: boolean;
  className?: string;
  children: React.ReactNode;
  'data-testid'?: string;
  'data-part-key'?: string;
  'data-box-no'?: number;
  'aria-label'?: string;
}

/**
 * CardLink — A card that renders as a proper anchor element for navigation.
 *
 * Uses TanStack Router's Link component to provide proper link semantics,
 * enabling Ctrl+Click / Cmd+Click to open in new tab, middle-click support,
 * and right-click context menu with "Open in new tab" option.
 *
 * Use this instead of Card with onClick when the card navigates to a route.
 */
export const CardLink = React.forwardRef<HTMLAnchorElement, CardLinkProps>(
  ({ disabled = false, className, children, to, params, search, ...props }, ref) => {
    const baseClasses = 'block rounded-lg border bg-card text-card-foreground shadow-sm p-4 overflow-hidden';

    const interactiveClasses = disabled
      ? 'pointer-events-none'
      : 'transition-all duration-200 hover:shadow-md hover:scale-[1.02] hover:border-primary/50 active:scale-[0.98] cursor-pointer';

    return (
      <Link
        ref={ref}
        to={to as '/'}
        params={params as Record<string, string>}
        search={search as Record<string, unknown>}
        className={cn(baseClasses, interactiveClasses, className)}
        tabIndex={disabled ? -1 : undefined}
        aria-disabled={disabled || undefined}
        {...props}
      >
        {children}
      </Link>
    );
  }
);

CardLink.displayName = 'CardLink';
