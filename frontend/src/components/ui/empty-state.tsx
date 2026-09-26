import * as React from 'react';
import { Button } from '@/components/primitives/button';
import { cn } from '@/lib/utils';

interface ActionConfig {
  label: string;
  onClick: () => void;
  testId?: string;
}

type EmptyStateDefaultProps = {
  variant?: 'default';
  testId: string;
  title: string;
  description?: string;
  icon?: React.ComponentType<{ className?: string }>;
  action?: ActionConfig | React.ReactNode;
  className?: string;
};

type EmptyStateMinimalProps = {
  variant: 'minimal';
  testId: string;
  title: string;
  description?: string;
  className?: string;
  // icon and action NOT supported in minimal variant
};

type EmptyStateProps = EmptyStateDefaultProps | EmptyStateMinimalProps;

function isActionConfig(action: ActionConfig | React.ReactNode): action is ActionConfig {
  return (
    typeof action === 'object' &&
    action !== null &&
    'label' in action &&
    'onClick' in action
  );
}

export const EmptyState = React.forwardRef<HTMLDivElement, EmptyStateProps>(
  (props, ref) => {
    const { testId, title, description, variant = 'default', className } = props;

    // Determine container base classes based on variant
    const containerClasses = cn(
      'text-center',
      variant === 'default'
        ? 'rounded-lg border border-dashed border-muted py-16'
        : 'rounded-md border border-dashed border-muted px-4 py-6',
      className
    );

    // Determine title classes based on variant
    const titleClasses =
      variant === 'default'
        ? 'text-lg font-semibold'
        : 'text-sm text-muted-foreground';

    // Determine description spacing based on variant
    const descriptionClasses =
      variant === 'default'
        ? 'mt-2 text-sm text-muted-foreground'
        : 'mt-1 text-sm text-muted-foreground';

    // Extract icon and action from props if variant is default
    const icon = variant === 'default' && 'icon' in props ? props.icon : undefined;
    const action = variant === 'default' && 'action' in props ? props.action : undefined;

    return (
      <div ref={ref} className={containerClasses} data-testid={testId}>
        {/* Render icon wrapper if icon provided and variant is default */}
        {icon && variant === 'default' && (
          <div className="mx-auto mb-4 flex h-24 w-24 items-center justify-center rounded-full bg-muted">
            {React.createElement(icon, { className: 'h-8 w-8 text-muted-foreground' })}
          </div>
        )}

        {/* Render title */}
        <h2 className={titleClasses}>{title}</h2>

        {/* Render description if provided */}
        {description && <p className={descriptionClasses}>{description}</p>}

        {/* Render action: structured ActionConfig renders a Button, ReactNode renders directly */}
        {action && variant === 'default' && (
          <div className="mt-4">
            {isActionConfig(action) ? (
              <Button
                onClick={action.onClick}
                data-testid={action.testId ?? `${testId}.cta`}
              >
                {action.label}
              </Button>
            ) : (
              action
            )}
          </div>
        )}
      </div>
    );
  }
);

EmptyState.displayName = 'EmptyState';
