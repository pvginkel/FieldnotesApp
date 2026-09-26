import { type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface DetailScreenLayoutProps {
  breadcrumbs?: ReactNode;
  title: ReactNode;
  titleMetadata?: ReactNode;
  description?: ReactNode;
  supplementary?: ReactNode;
  metadataRow?: ReactNode;
  actions?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  rootTestId?: string;
  headerTestId?: string;
  contentTestId?: string;
  footerTestId?: string;
  actionsTestId?: string;
  toolbarTestId?: string;
  headerProps?: HTMLAttributes<HTMLElement>;
  contentProps?: HTMLAttributes<HTMLDivElement>;
  footerProps?: HTMLAttributes<HTMLElement>;
  toolbarProps?: HTMLAttributes<HTMLElement>;
}

/**
 * Shared shell for detail screens where only the primary content scrolls.
 */
export function DetailScreenLayout({
  breadcrumbs,
  title,
  titleMetadata,
  description,
  supplementary,
  metadataRow,
  actions,
  toolbar,
  children,
  footer,
  className,
  rootTestId,
  headerTestId,
  contentTestId,
  footerTestId,
  actionsTestId,
  toolbarTestId,
  headerProps,
  contentProps,
  footerProps,
  toolbarProps,
}: DetailScreenLayoutProps) {
  const { className: headerClassName, ...headerRest } = headerProps ?? {};
  const { className: contentClassName, ...contentRest } = contentProps ?? {};
  const { className: footerClassName, ...footerRest } = footerProps ?? {};
  const { className: toolbarClassName, ...toolbarRest } = toolbarProps ?? {};
  const rootId = rootTestId ?? 'detail-screen.layout';
  const headerId = headerTestId ?? 'detail-screen.header';
  const contentId = contentTestId ?? 'detail-screen.content';
  const footerId = footerTestId ?? 'detail-screen.footer';
  const actionsId = actionsTestId ?? 'detail-screen.actions';
  const toolbarId = toolbarTestId ?? 'detail-screen.toolbar';

  return (
    <div className={cn('flex h-full min-h-0 flex-col', className)} data-testid={rootId}>
      {/* Header stays fixed above the scroll container */}
      <header
        className={cn('border-b border-border bg-background px-6 py-6', headerClassName)}
        data-testid={headerId}
        {...headerRest}
      >
        {breadcrumbs ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="detail-screen.breadcrumbs">
            {breadcrumbs}
          </div>
        ) : null}

        <div className="mt-4 flex flex-col gap-4 lg:mt-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-3xl font-semibold leading-tight text-foreground" data-testid="detail-screen.title">
                {title}
              </h1>
              {titleMetadata ? (
                <div data-testid="detail-screen.title-metadata">
                  {titleMetadata}
                </div>
              ) : null}
            </div>

            {description ? (
              <div className="text-sm text-muted-foreground" data-testid="detail-screen.description">
                {description}
              </div>
            ) : null}

            {supplementary ? (
              <div data-testid="detail-screen.supplementary">
                {supplementary}
              </div>
            ) : null}

            {metadataRow ? (
              <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="detail-screen.metadata">
                {metadataRow}
              </div>
            ) : null}
          </div>

          {actions ? (
            <div className="flex flex-wrap gap-2" data-testid={actionsId}>
              {actions}
            </div>
          ) : null}
        </div>
      </header>

      {toolbar ? (
        <section
          className={cn('border-b border-border bg-background px-6 py-4', toolbarClassName)}
          data-testid={toolbarId}
          {...toolbarRest}
        >
          {toolbar}
        </section>
      ) : null}

      <main
        className={cn('flex-1 min-h-0 overflow-auto bg-background px-6 py-6', contentClassName)}
        data-testid={contentId}
        role="main"
        {...contentRest}
      >
        {children}
      </main>

      {footer ? (
        <footer
          className={cn('border-t border-border bg-background px-6 py-4', footerClassName)}
          data-testid={footerId}
          {...footerRest}
        >
          {footer}
        </footer>
      ) : null}
    </div>
  );
}
