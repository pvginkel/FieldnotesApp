import * as React from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';
import { useTooltip } from './use-tooltip';

export interface TooltipProps {
  // Content (mutually exclusive - provide one or the other)
  title?: string; // Simple text tooltip (uses native title attribute on wrapper)
  content?: React.ReactNode; // Rich tooltip content (icons, formatting, multiline) - uses portal

  // Common props
  children: React.ReactElement; // Trigger element (must accept ref)
  enabled?: boolean; // Whether tooltip is enabled (default: true)
  placement?: 'top' | 'right' | 'bottom' | 'left' | 'auto' | 'center';
  showArrow?: boolean; // Show arrow pointing at anchor (default: true for content mode, false for center placement)
  offset?: number; // Distance from trigger (px) - ignored for 'center' placement
  delay?: number; // Open delay (ms)
  testId?: string; // data-testid for Playwright
}

export const Tooltip: React.FC<TooltipProps> = ({
  title,
  content,
  children,
  enabled = true,
  placement = 'auto',
  showArrow,
  offset = 8,
  delay = 200,
  testId,
}) => {
    // Validate props
    if (process.env.NODE_ENV === 'development') {
      if (title && content) {
        console.warn(
          'Tooltip: Both `title` and `content` props are provided. Only one should be used. `content` will take precedence.'
        );
      }
    }

    // Always call hooks at the top level
    const tooltipId = React.useId();
    const tooltip = useTooltip({
      placement,
      offset,
      openDelay: delay,
      closeDelay: 120, // Allow smooth mouse movement from trigger to tooltip content
    });

    // Determine if child is disabled
    const isChildDisabled =
      React.isValidElement(children) &&
      typeof children.props === 'object' &&
      children.props !== null &&
      'disabled' in children.props &&
      children.props.disabled === true;

    // Determine arrow visibility
    const shouldShowArrow = showArrow !== undefined ? showArrow : placement !== 'center';

    const handleMouseEnter = () => {
      if (enabled) {
        tooltip.open();
      }
    };

    const handleMouseLeave = () => {
      tooltip.close();
    };

    const handleFocus = () => {
      if (enabled) {
        tooltip.open();
      }
    };

    const handleBlur = () => {
      tooltip.close();
    };

    const handleKeyDown = (event: React.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        tooltip.close();
      }
    };

    // Close tooltip when enabled becomes false
    React.useEffect(() => {
      if (!enabled && tooltip.isOpen) {
        tooltip.close();
      }
    }, [enabled, tooltip]);

    // Title mode: Use native title attribute (lightweight)
    if (title && !content) {
      if (!enabled) {
        return children;
      }

      if (isChildDisabled) {
        // Wrapper pattern for disabled elements
        return (
          <div
            className="inline-flex cursor-not-allowed"
            tabIndex={0}
            title={title}
            data-testid={testId}
          >
            {React.cloneElement(children as React.ReactElement<any>, {
              className: cn((children as React.ReactElement<any>).props?.className, 'pointer-events-none'),
            })}
          </div>
        );
      }

      // For enabled elements, just add title to the child
      return React.cloneElement(children as React.ReactElement<any>, {
        title,
        'data-testid': testId,
      });
    }

    // Content mode: Portal-based rich tooltip
    if (content) {
      if (!enabled) {
        return children;
      }

      // Check if child already has a testId
      const childHasTestId = React.isValidElement(children) &&
        typeof children.props === 'object' &&
        children.props !== null &&
        'data-testid' in children.props;

      // Wrapper element that captures events
      // Use .disabled-wrapper suffix when child is disabled, otherwise use base testId (but only if child doesn't have one)
      const wrapperTestId = isChildDisabled && testId
        ? `${testId}.disabled-wrapper`
        : (!childHasTestId ? testId : undefined);

      const wrapper = (
        <div
          ref={tooltip.triggerRef as React.RefObject<HTMLDivElement>}
          className={cn('inline-flex', isChildDisabled && 'cursor-not-allowed')}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
          onFocus={handleFocus}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          tabIndex={isChildDisabled ? 0 : undefined}
          // eslint-disable-next-line react-hooks/refs -- tooltip.isOpen is state, not ref
          aria-describedby={tooltip.isOpen ? tooltipId : undefined}
          data-testid={wrapperTestId}
        >
          {React.cloneElement(children as React.ReactElement<any>, {
            className: cn((children as React.ReactElement<any>).props?.className, isChildDisabled && 'pointer-events-none'),
          })}
        </div>
      );

      // Render tooltip content via portal
      /* eslint-disable react-hooks/refs -- tooltip object contains state values (isOpen, position, arrowPosition), not refs */
      const tooltipContent = tooltip.isOpen
        ? createPortal(
            <div
              ref={tooltip.tooltipRef}
              id={tooltipId}
              role="tooltip"
              className={cn(
                'z-50 rounded-md border border-border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-md',
                'animate-in fade-in-0 zoom-in-95',
                'data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95'
              )}
              style={{
                position: 'fixed',
                top: tooltip.position.top,
                left: tooltip.position.left,
              }}
              data-testid={testId ? `${testId}.tooltip` : undefined}
              onMouseEnter={handleMouseEnter}
              onMouseLeave={handleMouseLeave}
            >
              {content}
              {shouldShowArrow && tooltip.arrowPosition && (
                <div
                  className={cn(
                    'absolute h-2 w-2 rotate-45 border-border bg-popover',
                    tooltip.arrowPosition === 'top' && 'top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 border-t border-l',
                    tooltip.arrowPosition === 'bottom' && 'bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2 border-b border-r',
                    tooltip.arrowPosition === 'left' && 'left-0 top-1/2 -translate-x-1/2 -translate-y-1/2 border-l border-b',
                    tooltip.arrowPosition === 'right' && 'right-0 top-1/2 translate-x-1/2 -translate-y-1/2 border-r border-t'
                  )}
                  aria-hidden="true"
                />
              )}
            </div>,
            document.body
          )
        : null;
      /* eslint-enable react-hooks/refs */

      return (
        <>
          {wrapper}
          {tooltipContent}
        </>
      );
    }

  // No tooltip provided
  return children;
};

Tooltip.displayName = 'Tooltip';
