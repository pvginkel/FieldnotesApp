import { type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Card } from '@/components/primitives/card';

interface FormScreenLayoutProps {
  breadcrumbs?: ReactNode;
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  rootTestId?: string;
  headerTestId?: string;
  contentTestId?: string;
  footerTestId?: string;
  actionsTestId?: string;
  cardTestId?: string;
  headerProps?: HTMLAttributes<HTMLElement>;
  contentProps?: HTMLAttributes<HTMLElement>;
  footerProps?: HTMLAttributes<HTMLElement>;
}

/**
 * Form screens keep the header and footer pinned while the form body scrolls.
 */
export function FormScreenLayout({
  breadcrumbs,
  title,
  actions,
  children,
  footer,
  className,
  rootTestId,
  headerTestId,
  contentTestId,
  footerTestId,
  actionsTestId,
  cardTestId,
  headerProps,
  contentProps,
  footerProps,
}: FormScreenLayoutProps) {
  const { className: headerClassName, ...headerRest } = headerProps ?? {};
  const { className: contentClassName, ...contentRest } = contentProps ?? {};
  const { className: footerClassName, ...footerRest } = footerProps ?? {};
  const rootId = rootTestId ?? 'form-screen.layout';
  const cardId = cardTestId ?? 'form-screen.card';
  const headerId = headerTestId ?? 'form-screen.header';
  const contentId = contentTestId ?? 'form-screen.content';
  const footerId = footerTestId ?? 'form-screen.footer';
  const actionsId = actionsTestId ?? 'form-screen.actions';

  return (
    <div className={cn('flex h-full min-h-0 flex-col', className)} data-testid={rootId}>
      <Card className="flex h-full min-h-0 flex-col overflow-hidden p-0" data-testid={cardId}>
        {/* Header anchors breadcrumbs, title, and actions inside the card chrome */}
        <header
          className={cn('border-b border-border bg-background px-6 py-5', headerClassName)}
          data-testid={headerId}
          {...headerRest}
        >
          {breadcrumbs ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="form-screen.breadcrumbs">
              {breadcrumbs}
            </div>
          ) : null}

          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <h1 className="text-3xl font-semibold leading-tight text-foreground" data-testid="form-screen.title">
              {title}
            </h1>

            {actions ? (
              <div className="flex flex-wrap items-center gap-2" data-testid={actionsId}>
                {actions}
              </div>
            ) : null}
          </div>
        </header>

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
            className={cn('border-t border-border bg-background px-6 py-5', footerClassName)}
            data-testid={footerId}
            {...footerRest}
          >
            {footer}
          </footer>
        ) : null}
      </Card>
    </div>
  );
}
