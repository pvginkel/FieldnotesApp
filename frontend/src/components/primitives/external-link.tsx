import * as React from 'react';
import { cn } from '@/lib/utils';
import { ExternalLinkIcon } from '@/components/icons/ExternalLinkIcon';

export interface ExternalLinkProps {
  href: string;
  children?: React.ReactNode;
  className?: string;
  onClick?: (e: React.MouseEvent<HTMLAnchorElement>) => void;
  ariaLabel?: string;
  testId?: string;
}

export const ExternalLink = React.forwardRef<HTMLAnchorElement, ExternalLinkProps>(
  (
    {
      href,
      children,
      className,
      onClick,
      ariaLabel,
      testId,
    },
    ref
  ) => {
    // Runtime validation in development mode
    if (process.env.NODE_ENV === 'development') {
      if (!href || href.trim() === '') {
        console.warn('ExternalLink: href prop is empty or invalid');
      }
      if (href && !href.startsWith('http://') && !href.startsWith('https://')) {
        console.warn(
          `ExternalLink: href "${href}" does not start with http:// or https://. This may be a security risk.`
        );
      }
    }

    // If href is invalid, render as disabled span
    if (!href || href.trim() === '') {
      return (
        <span
          className={cn('text-muted-foreground cursor-not-allowed break-all', className)}
          title="Invalid link"
        >
          {children || 'Invalid link'}
        </span>
      );
    }

    const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
      try {
        onClick?.(e);
      } catch (error) {
        console.error('ExternalLink onClick handler error:', error);
      }
    };

    return (
      <a
        ref={ref}
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        onClick={handleClick}
        className={cn(
          'inline-flex items-center gap-1',
          'text-link hover:underline',
          className
        )}
        aria-label={ariaLabel}
        data-testid={testId}
      >
        {children}
        <ExternalLinkIcon className="inline w-3 h-3 flex-shrink-0" />
      </a>
    );
  }
);

ExternalLink.displayName = 'ExternalLink';
