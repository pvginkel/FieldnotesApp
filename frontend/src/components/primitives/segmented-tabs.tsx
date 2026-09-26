import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from 'react';
import { cn } from '@/lib/utils';

type TabElement = HTMLButtonElement;

const useIsomorphicLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

export interface SegmentedTabItem {
  id: string;
  label: string;
  count?: number;
  countLabel?: string;
  panelId?: string;
  testId?: string;
  disabled?: boolean;
}

interface SegmentedTabsProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'onChange'> {
  items: SegmentedTabItem[];
  value: string;
  onValueChange: (value: string) => void;
  ariaLabel?: string;
  ariaLabelledBy?: string;
  uppercase?: boolean;
  scrollable?: boolean;
}

/**
 * SegmentedTabs renders an accessible `role="tablist"` surface with animated highlighting.
 * The component is controlled so the parent can persist selection or sync query params.
 */
export function SegmentedTabs({
  items,
  value,
  onValueChange,
  ariaLabel,
  ariaLabelledBy,
  className,
  uppercase = false,
  scrollable = true,
  ...props
}: SegmentedTabsProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef(new Map<string, TabElement>());
  const [indicatorStyle, setIndicatorStyle] = useState<{ width: number; left: number } | null>(null);

  const activeIndex = useMemo(
    () => items.findIndex((item) => item.id === value && !item.disabled),
    [items, value],
  );

  const enabledIndices = useMemo(() => {
    const enabled: number[] = [];
    items.forEach((item, index) => {
      if (!item.disabled) {
        enabled.push(index);
      }
    });
    return enabled;
  }, [items]);

  const setTabRef = useCallback(
    (id: string) => (node: TabElement | null) => {
      if (node) {
        tabRefs.current.set(id, node);
      } else {
        tabRefs.current.delete(id);
      }
    },
    [],
  );

  const updateIndicator = useCallback(() => {
    const container = containerRef.current;
    if (!container) {
      setIndicatorStyle(null);
      return;
    }

    const activeTab = tabRefs.current.get(value);
    if (!activeTab) {
      setIndicatorStyle(null);
      return;
    }

    const containerRect = container.getBoundingClientRect();
    const activeRect = activeTab.getBoundingClientRect();
    const left = activeRect.left - containerRect.left + container.scrollLeft;

    setIndicatorStyle({
      width: activeRect.width,
      left,
    });
  }, [value]);

  // Keep the indicator aligned as the layout resizes or scrolls.
  useIsomorphicLayoutEffect(() => {
    updateIndicator();
  }, [items, updateIndicator]);

  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') {
      return;
    }

    const observer = new ResizeObserver(() => updateIndicator());
    tabRefs.current.forEach((node) => observer.observe(node));

    return () => observer.disconnect();
  }, [items, updateIndicator]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const handleScroll = () => updateIndicator();
    container.addEventListener('scroll', handleScroll, { passive: true });

    return () => {
      container.removeEventListener('scroll', handleScroll);
    };
  }, [updateIndicator]);

  useEffect(() => {
    const handleResize = () => updateIndicator();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [updateIndicator]);

  const focusTabAtIndex = useCallback(
    (targetIndex: number) => {
      const item = items[targetIndex];
      if (!item || item.disabled) {
        return;
      }
      const node = tabRefs.current.get(item.id);
      if (!node) {
        return;
      }

      node.focus();

      if (scrollable) {
        node.scrollIntoView({
          behavior: 'auto',
          block: 'nearest',
          inline: 'center',
        });
      }
    },
    [items, scrollable],
  );

  const cycleIndex = useCallback(
    (startIndex: number, direction: 1 | -1) => {
      if (enabledIndices.length === 0) {
        return startIndex;
      }

      const currentPosition = enabledIndices.indexOf(startIndex);
      if (currentPosition === -1) {
        return enabledIndices[direction === 1 ? 0 : enabledIndices.length - 1];
      }

      const nextPosition = (currentPosition + direction + enabledIndices.length) % enabledIndices.length;
      return enabledIndices[nextPosition];
    },
    [enabledIndices],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<TabElement>, index: number) => {
      if (!enabledIndices.includes(index)) {
        return;
      }

      switch (event.key) {
        case 'ArrowRight':
        case 'ArrowDown': {
          event.preventDefault();
          const nextIndex = cycleIndex(index, 1);
          focusTabAtIndex(nextIndex);
          break;
        }
        case 'ArrowLeft':
        case 'ArrowUp': {
          event.preventDefault();
          const nextIndex = cycleIndex(index, -1);
          focusTabAtIndex(nextIndex);
          break;
        }
        case 'Home': {
          event.preventDefault();
          const homeIndex = enabledIndices.at(0);
          if (homeIndex !== undefined) {
            focusTabAtIndex(homeIndex);
          }
          break;
        }
        case 'End': {
          event.preventDefault();
          const endIndex = enabledIndices.at(-1);
          if (endIndex !== undefined) {
            focusTabAtIndex(endIndex);
          }
          break;
        }
        case 'Enter':
        case ' ': {
          event.preventDefault();
          const item = items[index];
          if (!item?.disabled) {
            onValueChange(item.id);
          }
          break;
        }
        default:
      }
    },
    [cycleIndex, enabledIndices, focusTabAtIndex, items, onValueChange],
  );

  const handleClick = useCallback(
    (event: MouseEvent<TabElement>, item: SegmentedTabItem) => {
      if (item.disabled) {
        event.preventDefault();
        return;
      }
      onValueChange(item.id);
    },
    [onValueChange],
  );

  const fallbackIndex = enabledIndices[0] ?? -1;

  return (
    <div
      {...props}
      role="tablist"
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
      ref={containerRef}
      className={cn(
        'relative isolate flex w-full max-w-full rounded-full border border-border bg-muted/60 p-1 text-foreground shadow-sm backdrop-blur',
        scrollable && 'overflow-x-auto overscroll-x-contain scroll-smooth snap-x snap-mandatory',
        className,
      )}
    >
      {indicatorStyle && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-1 rounded-full bg-primary/30 shadow transition-transform duration-200 ease-out"
          style={{
            width: `${indicatorStyle.width - 8}px`,
            transform: `translateX(${indicatorStyle.left}px)`,
          }}
        />
      )}
      {items.map((item, index) => {
        const isActive = item.id === value;
        const isDisabled = Boolean(item.disabled);
        const tabIndex =
          isActive && !isDisabled
            ? 0
            : activeIndex === -1 && index === fallbackIndex
              ? 0
              : -1;
        const labelText = uppercase ? item.label.toUpperCase() : item.label;
        const countText =
          typeof item.count === 'number' ? item.count.toLocaleString() : null;
        const ariaControls = item.panelId ?? undefined;

        return (
          <button
            key={item.id}
            ref={setTabRef(item.id)}
            role="tab"
            type="button"
            aria-selected={isActive}
            aria-controls={ariaControls}
            aria-disabled={isDisabled || undefined}
            data-testid={item.testId}
            className={cn(
              'relative z-10 flex shrink-0 items-center gap-2 rounded-full px-4 py-1.5 text-sm font-medium leading-none tracking-tight transition-colors duration-150 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
              scrollable ? 'snap-start' : undefined,
              uppercase ? 'uppercase tracking-[0.08em]' : undefined,
              isDisabled
                ? 'cursor-not-allowed text-muted-foreground/70'
                : 'cursor-pointer',
              isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
            tabIndex={tabIndex}
            onKeyDown={(event) => handleKeyDown(event, index)}
            onClick={(event) => handleClick(event, item)}
          >
            <span className="whitespace-nowrap">{labelText}</span>
            {countText !== null && (
              <span
                className={cn(
                  'rounded-full px-2 py-0.5 text-[0.7rem] font-medium leading-none transition-colors duration-150',
                  isActive
                    ? 'bg-background/90 text-foreground shadow-sm'
                    : 'text-muted-foreground',
                  isDisabled && 'opacity-60',
                )}
              >
                {countText}
                {item.countLabel ? (
                  <span className="sr-only"> {item.countLabel}</span>
                ) : null}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
