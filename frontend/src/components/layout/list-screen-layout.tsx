import { type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface ListScreenLayoutProps {
  breadcrumbs?: ReactNode;
  title: ReactNode;
  actions?: ReactNode;
  search?: ReactNode;
  filters?: ReactNode;
  segmentedTabs?: ReactNode;
  counts?: ReactNode;
  children: ReactNode;
  headerTestId?: string;
  contentTestId?: string;
  contentProps?: HTMLAttributes<HTMLDivElement>;
  className?: string;
  rootTestId?: string;
  headerProps?: HTMLAttributes<HTMLElement>;
}

/**
 * Shared shell for list screens where the header remains sticky and the body scrolls.
 */
export function ListScreenLayout({
  breadcrumbs,
  title,
  actions,
  search,
  filters,
  segmentedTabs,
  counts,
  children,
  headerTestId,
  contentTestId,
  contentProps,
  className,
  rootTestId,
  headerProps,
}: ListScreenLayoutProps) {
  const { className: contentClassName, ...contentRest } = contentProps ?? {};
  const { className: headerClassName, ...headerRest } = headerProps ?? {};

  return (
    <div className={cn('flex h-full min-h-0 flex-col', className)} data-testid={rootTestId}>
      {/* Sticky header captures breadcrumbs, titles, filters, and counts */}
      <header
        className={cn(
          'sticky top-0 z-20 border-b border-border shadow-sm backdrop-blur',
          headerClassName,
        )}
        data-testid={headerTestId}
        {...headerRest}
      >
        <div className="space-y-4 px-6 py-4">
          {breadcrumbs && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="list-screen.breadcrumbs">
              {breadcrumbs}
            </div>
          )}

          <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
            <div className="text-3xl font-bold leading-tight" data-testid="list-screen.title">
              {title}
            </div>
            {actions && (
              <div className="flex flex-wrap gap-2" data-testid="list-screen.actions">
                {actions}
              </div>
            )}
          </div>

          {search && (
            <div className="w-full" data-testid="list-screen.search">
              {search}
            </div>
          )}

          {filters && (
            <div className="w-full" data-testid="list-screen.filters">
              {filters}
            </div>
          )}

          {segmentedTabs && (
            <div className="w-full" data-testid="list-screen.tabs">
              {segmentedTabs}
            </div>
          )}

          {counts && (
            <div data-testid="list-screen.counts">
              {counts}
            </div>
          )}
        </div>
      </header>

      {/* Scrollable content region owns the remainder of the viewport */}
      <div
        className={cn('flex-1 min-h-0 overflow-auto px-6 py-6', contentClassName)}
        data-testid={contentTestId}
        {...contentRest}
      >
        {children}
      </div>
    </div>
  );
}
