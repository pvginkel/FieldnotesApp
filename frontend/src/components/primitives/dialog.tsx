import * as React from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { Button } from './button'
import { cn } from '@/lib/utils'

type DialogRootProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Root>
type DialogOverlayProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
export type DialogContentProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
  'data-testid'?: string
}
type DialogPortalProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Portal>

/**
 * Dialog - Accessible modal dialog component built on Radix UI
 *
 * This component uses a prop distribution pattern to allow full customization
 * of each Radix primitive while maintaining a clean API.
 *
 * Prop Distribution:
 * - Root props: Control the dialog state (open, onOpenChange, etc.)
 * - overlayProps: Customize the backdrop overlay
 * - contentProps: Customize the dialog content container (receives data-testid)
 * - portalProps: Control the rendering portal
 * - className: Maps to contentProps.className for backward compatibility
 *
 * The ref is forwarded to DialogPrimitive.Content as it's the main interactive element.
 *
 * @example
 * ```tsx
 * <Dialog
 *   open={isOpen}
 *   onOpenChange={setIsOpen}
 *   contentProps={{ 'data-testid': 'my-dialog' }}
 * >
 *   <DialogContent>...</DialogContent>
 * </Dialog>
 * ```
 */
interface DialogProps extends DialogRootProps {
  /** Props for the overlay/backdrop element */
  overlayProps?: DialogOverlayProps
  /** Props for the content container (receives data-testid) */
  contentProps?: DialogContentProps
  /** Props for the portal element */
  portalProps?: DialogPortalProps
  /** Backward compatibility - maps to contentProps.className */
  className?: string
}

export const Dialog = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  DialogProps
>(({ overlayProps, contentProps, portalProps, className, children, ...rootProps }, ref) => {
  return (
    <DialogPrimitive.Root {...rootProps}>
      <DialogPrimitive.Portal {...portalProps}>
        <DialogPrimitive.Overlay
          {...overlayProps}
          className={cn(
            "fixed inset-0 z-50 bg-black/50 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            overlayProps?.className
          )}
        />
        <DialogPrimitive.Content
          ref={ref}
          {...contentProps}
          className={cn(
            "fixed left-[50%] top-[50%] z-50 translate-x-[-50%] translate-y-[-50%] bg-card text-card-foreground border rounded-lg shadow-lg data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[state=closed]:slide-out-to-left-1/2 data-[state=closed]:slide-out-to-top-[48%] data-[state=open]:slide-in-from-left-1/2 data-[state=open]:slide-in-from-top-[48%] max-w-lg w-full",
            className,
            contentProps?.className
          )}
        >
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
})

Dialog.displayName = 'Dialog'

type NativeDivProps = React.ComponentPropsWithoutRef<"div">

type DialogInnerContentProps = NativeDivProps

export const DialogInnerContent = React.forwardRef<HTMLDivElement, DialogInnerContentProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        {...props}
        className={cn("p-6", className)}
      >
        {children}
      </div>
    )
  }
)

DialogInnerContent.displayName = 'DialogInnerContent'

type DialogHeaderProps = NativeDivProps

export const DialogHeader = React.forwardRef<HTMLDivElement, DialogHeaderProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        {...props}
        className={cn("flex flex-col space-y-1.5 pb-4", className)}
      >
        {children}
      </div>
    )
  }
)

DialogHeader.displayName = 'DialogHeader'

type DialogTitlePrimitiveProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>

type DialogTitleProps = DialogTitlePrimitiveProps

export const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  DialogTitleProps
>(({ className, children, ...props }, ref) => {
  return (
    <DialogPrimitive.Title
      ref={ref}
      {...props}
      className={cn("text-lg font-semibold leading-none tracking-tight", className)}
    >
      {children}
    </DialogPrimitive.Title>
  )
})

DialogTitle.displayName = 'DialogTitle'

type DialogFooterProps = NativeDivProps

export const DialogFooter = React.forwardRef<HTMLDivElement, DialogFooterProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        {...props}
        className={cn("flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2 pt-4", className)}
      >
        {children}
      </div>
    )
  }
)

DialogFooter.displayName = 'DialogFooter'

type DialogDescriptionPrimitiveProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>

type DialogDescriptionProps = DialogDescriptionPrimitiveProps

export const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  DialogDescriptionProps
>(({ className, children, ...props }, ref) => {
  return (
    <DialogPrimitive.Description
      ref={ref}
      {...props}
      className={cn("text-sm text-muted-foreground", className)}
    >
      {children}
    </DialogPrimitive.Description>
  )
})

DialogDescription.displayName = 'DialogDescription'

// Compatibility exports
export const DialogContent = DialogInnerContent

export interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmText?: string
  cancelText?: string
  onConfirm: () => void
  destructive?: boolean
  contentProps?: DialogContentProps
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  onConfirm,
  destructive = false,
  contentProps
}: ConfirmDialogProps) {
  const handleConfirm = (e: React.MouseEvent) => {
    // Prevent event from bubbling to parent elements (e.g., clickable cards)
    e.stopPropagation()
    onConfirm()
    onOpenChange(false)
  }

  const handleCancel = (e: React.MouseEvent) => {
    // Prevent event from bubbling to parent elements
    e.stopPropagation()
    onOpenChange(false)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      contentProps={contentProps}
    >
      <DialogInnerContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={handleCancel}>
            {cancelText}
          </Button>
          <Button
            variant={destructive ? 'default' : 'primary'}
            onClick={handleConfirm}
            className={destructive ? 'bg-destructive text-destructive-foreground hover:bg-destructive/90' : ''}
          >
            {confirmText}
          </Button>
        </DialogFooter>
      </DialogInnerContent>
    </Dialog>
  )
}
