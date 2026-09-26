import * as React from 'react';
import { cn } from '@/lib/utils';

type NativeSpanProps = React.ComponentPropsWithoutRef<"span">

interface BadgeProps extends NativeSpanProps {
  variant?: 'default' | 'secondary' | 'destructive' | 'outline';
}

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ variant = 'default', className, children, ...props }, ref) => {
    const variantClasses = {
      default: 'bg-primary text-primary-foreground',
      secondary: 'bg-secondary text-secondary-foreground',
      destructive: 'bg-destructive text-destructive-foreground',
      outline: 'text-foreground border border-input bg-background',
    };

    return (
      <span
        ref={ref}
        {...props}
        className={cn(
          'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-hidden focus:ring-2 focus:ring-ring focus:ring-offset-2',
          variantClasses[variant],
          className
        )}
      >
        {children}
      </span>
    );
  }
)

Badge.displayName = 'Badge'