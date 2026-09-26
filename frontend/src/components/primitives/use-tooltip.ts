import { useState, useRef, useCallback, useLayoutEffect, type RefObject } from 'react';

export interface UseTooltipOptions {
  placement?: 'top' | 'right' | 'bottom' | 'left' | 'auto' | 'center';
  offset?: number;
  openDelay?: number;
  closeDelay?: number;
}

export interface UseTooltipReturn {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  toggle: () => void;
  triggerRef: RefObject<HTMLElement | null>;
  tooltipRef: RefObject<HTMLDivElement | null>;
  position: { top: number; left: number };
  arrowPosition: 'top' | 'bottom' | 'left' | 'right' | null;
}

export function useTooltip(options: UseTooltipOptions = {}): UseTooltipReturn {
  const {
    placement = 'auto',
    offset = 8,
    openDelay = 200,
    closeDelay = 120,
  } = options;

  const [isOpen, setIsOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const [arrowPosition, setArrowPosition] = useState<'top' | 'bottom' | 'left' | 'right' | null>(null);

  const triggerRef = useRef<HTMLElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const openTimerRef = useRef<number | null>(null);
  const closeTimerRef = useRef<number | null>(null);

  const cancelOpenTimer = useCallback(() => {
    if (openTimerRef.current) {
      window.clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
  }, []);

  const cancelCloseTimer = useCallback(() => {
    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const tooltip = tooltipRef.current;

    if (!trigger || !tooltip) {
      return;
    }

    const triggerRect = trigger.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const padding = 12;

    // For center placement, position tooltip over the trigger element (modal-like)
    if (placement === 'center') {
      const top = triggerRect.top + (triggerRect.height - tooltipRect.height) / 2;
      const left = triggerRect.left + (triggerRect.width - tooltipRect.width) / 2;

      setPosition({
        top: Math.max(padding, Math.min(top, window.innerHeight - tooltipRect.height - padding)),
        left: Math.max(padding, Math.min(left, window.innerWidth - tooltipRect.width - padding)),
      });
      setArrowPosition(null);
      return;
    }

    // Calculate preferred placement
    let preferredPlacement = placement;

    if (placement === 'auto') {
      // Auto-detect best placement based on available space
      const spaceTop = triggerRect.top;
      const spaceBottom = window.innerHeight - triggerRect.bottom;
      const spaceLeft = triggerRect.left;
      const spaceRight = window.innerWidth - triggerRect.right;

      const maxSpace = Math.max(spaceTop, spaceBottom, spaceLeft, spaceRight);

      if (maxSpace === spaceBottom && spaceBottom >= tooltipRect.height + offset) {
        preferredPlacement = 'bottom';
      } else if (maxSpace === spaceTop && spaceTop >= tooltipRect.height + offset) {
        preferredPlacement = 'top';
      } else if (maxSpace === spaceRight && spaceRight >= tooltipRect.width + offset) {
        preferredPlacement = 'right';
      } else if (maxSpace === spaceLeft && spaceLeft >= tooltipRect.width + offset) {
        preferredPlacement = 'left';
      } else {
        preferredPlacement = 'bottom'; // fallback
      }
    }

    // Calculate position based on placement
    let top = 0;
    let left = 0;
    let arrow: 'top' | 'bottom' | 'left' | 'right' = 'bottom';

    switch (preferredPlacement) {
      case 'top':
        top = triggerRect.top - tooltipRect.height - offset;
        left = triggerRect.left + (triggerRect.width - tooltipRect.width) / 2;
        arrow = 'bottom';
        break;
      case 'bottom':
        top = triggerRect.bottom + offset;
        left = triggerRect.left + (triggerRect.width - tooltipRect.width) / 2;
        arrow = 'top';
        break;
      case 'left':
        top = triggerRect.top + (triggerRect.height - tooltipRect.height) / 2;
        left = triggerRect.left - tooltipRect.width - offset;
        arrow = 'right';
        break;
      case 'right':
        top = triggerRect.top + (triggerRect.height - tooltipRect.height) / 2;
        left = triggerRect.right + offset;
        arrow = 'left';
        break;
    }

    // Keep tooltip within viewport bounds
    const maxLeft = window.innerWidth - tooltipRect.width - padding;
    const maxTop = window.innerHeight - tooltipRect.height - padding;

    left = Math.min(Math.max(padding, left), Math.max(padding, maxLeft));
    top = Math.min(Math.max(padding, top), Math.max(padding, maxTop));

    setPosition({ top, left });
    setArrowPosition(arrow);
  }, [placement, offset]);

  // Update position when tooltip opens or on scroll/resize
  useLayoutEffect(() => {
    if (!isOpen) {
      return;
    }

    updatePosition();

    const handleScroll = () => updatePosition();
    const handleResize = () => updatePosition();

    window.addEventListener('scroll', handleScroll, true);
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('scroll', handleScroll, true);
      window.removeEventListener('resize', handleResize);
    };
  }, [isOpen, updatePosition]);

  // Cleanup timers on unmount
  useLayoutEffect(() => {
    return () => {
      cancelOpenTimer();
      cancelCloseTimer();
    };
  }, [cancelOpenTimer, cancelCloseTimer]);

  const open = useCallback(() => {
    cancelCloseTimer();
    cancelOpenTimer();

    if (openDelay > 0) {
      openTimerRef.current = window.setTimeout(() => {
        setIsOpen(true);
      }, openDelay);
    } else {
      setIsOpen(true);
    }
  }, [openDelay, cancelCloseTimer, cancelOpenTimer]);

  const close = useCallback(() => {
    cancelOpenTimer();
    cancelCloseTimer();

    if (closeDelay > 0) {
      closeTimerRef.current = window.setTimeout(() => {
        setIsOpen(false);
      }, closeDelay);
    } else {
      setIsOpen(false);
    }
  }, [closeDelay, cancelOpenTimer, cancelCloseTimer]);

  const toggle = useCallback(() => {
    if (isOpen) {
      close();
    } else {
      open();
    }
  }, [isOpen, open, close]);

  return {
    isOpen,
    open,
    close,
    toggle,
    triggerRef,
    tooltipRef,
    position,
    arrowPosition,
  };
}
