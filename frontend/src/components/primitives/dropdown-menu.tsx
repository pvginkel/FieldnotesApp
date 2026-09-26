import * as React from 'react';
import type { ReactNode } from 'react';
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';
import { cn } from '@/lib/utils';

type NativeDropdownMenuRootProps = React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Root>;

interface DropdownMenuProps extends NativeDropdownMenuRootProps {
  children: ReactNode;
}

export function DropdownMenu({ children, ...props }: DropdownMenuProps) {
  return (
    <DropdownMenuPrimitive.Root {...props}>
      {children}
    </DropdownMenuPrimitive.Root>
  );
}

type NativeDropdownMenuTriggerProps = React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Trigger>;

interface DropdownMenuTriggerProps extends NativeDropdownMenuTriggerProps {
  children: ReactNode;
}

export const DropdownMenuTrigger = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Trigger>,
  DropdownMenuTriggerProps
>(({ children, className, ...props }, ref) => {
  return (
    <DropdownMenuPrimitive.Trigger
      ref={ref}
      {...props}
      className={className}
    >
      {children}
    </DropdownMenuPrimitive.Trigger>
  );
});
DropdownMenuTrigger.displayName = "DropdownMenuTrigger";

type NativeDropdownMenuContentProps = React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Content>;

interface DropdownMenuContentProps extends Omit<NativeDropdownMenuContentProps, 'align' | 'sideOffset'> {
  align?: 'start' | 'center' | 'end';
  sideOffset?: number;
  children: ReactNode;
}

export const DropdownMenuContent = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Content>,
  DropdownMenuContentProps
>(({ align = 'start', sideOffset = 4, className, children, ...props }, ref) => {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        ref={ref}
        {...props}
        align={align}
        sideOffset={sideOffset}
        className={cn(
          'z-50 min-w-[8rem] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md',
          'data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2',
          className
        )}
      >
        {children}
      </DropdownMenuPrimitive.Content>
    </DropdownMenuPrimitive.Portal>
  );
});
DropdownMenuContent.displayName = "DropdownMenuContent";

type NativeDropdownMenuItemProps = React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Item>;

interface DropdownMenuItemProps extends Omit<NativeDropdownMenuItemProps, 'onSelect'> {
  onClick?: () => void;
  children: ReactNode;
}

export const DropdownMenuItem = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Item>,
  DropdownMenuItemProps
>(({ onClick, className, children, ...props }, ref) => {
  return (
    <DropdownMenuPrimitive.Item
      ref={ref}
      {...props}
      onSelect={onClick}
      className={cn(
        'relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-hidden transition-colors',
        'focus:bg-accent focus:text-accent-foreground',
        'data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[disabled]:cursor-default',
        'hover:bg-accent hover:text-accent-foreground w-full text-left',
        className
      )}
    >
      {children}
    </DropdownMenuPrimitive.Item>
  );
});
DropdownMenuItem.displayName = "DropdownMenuItem";

type NativeDropdownMenuSeparatorProps = React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Separator>;

type DropdownMenuSeparatorProps = NativeDropdownMenuSeparatorProps;

export const DropdownMenuSeparator = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Separator>,
  DropdownMenuSeparatorProps
>(({ className, ...props }, ref) => {
  return (
    <DropdownMenuPrimitive.Separator
      ref={ref}
      {...props}
      className={cn('-mx-1 my-1 h-px bg-muted', className)}
    />
  );
});
DropdownMenuSeparator.displayName = "DropdownMenuSeparator";

type NativeDropdownMenuLabelProps = React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Label>;

interface DropdownMenuLabelProps extends NativeDropdownMenuLabelProps {
  children: ReactNode;
}

export const DropdownMenuLabel = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Label>,
  DropdownMenuLabelProps
>(({ className, children, ...props }, ref) => {
  return (
    <DropdownMenuPrimitive.Label
      ref={ref}
      {...props}
      className={cn('px-2 py-1.5 text-sm font-semibold', className)}
    >
      {children}
    </DropdownMenuPrimitive.Label>
  );
});
DropdownMenuLabel.displayName = "DropdownMenuLabel";