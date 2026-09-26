import * as React from 'react'
import * as ToastPrimitive from '@radix-ui/react-toast'
import { cn } from '@/lib/utils'
import { ClearButtonIcon } from '@/components/icons/clear-button-icon'
import { toastToneStyles, toastIconMap } from '@/styles/toast'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface ToastAction {
  id: string
  label: string
  onClick?: () => void
  testId?: string
}

export interface ToastOptions {
  duration?: number
  action?: ToastAction
}

export interface Toast {
  id: string
  type: ToastType
  message: string
  duration?: number
  action?: ToastAction
}

type NativeToastRootProps = React.ComponentPropsWithoutRef<typeof ToastPrimitive.Root>;
type NativeToastViewportProps = React.ComponentPropsWithoutRef<typeof ToastPrimitive.Viewport>;

interface ToastProps extends Omit<NativeToastRootProps, 'duration' | 'onOpenChange'> {
  toast: Toast
  onRemove: (id: string) => void
}

const DEFAULT_TOAST_DURATION_MS = 15000

const ToastComponent = React.forwardRef<
  React.ElementRef<typeof ToastPrimitive.Root>,
  ToastProps
>(({ toast, onRemove, className, ...props }, ref) => {
  const tone = toastToneStyles[toast.type];

  return (
    <ToastPrimitive.Root
      ref={ref}
      {...props}
      duration={toast.duration ?? DEFAULT_TOAST_DURATION_MS}
      role="status"
      aria-live="polite"
      data-toast-type={toast.type}
      onOpenChange={(open) => {
        if (!open) {
          onRemove(toast.id)
        }
      }}
      className={cn(
        'group pointer-events-auto relative w-full max-w-sm overflow-hidden rounded-lg border shadow-lg transition-all',
        'data-[state=open]:animate-in data-[state=closed]:animate-out data-[swipe=end]:animate-out',
        'data-[state=closed]:fade-out-80 data-[state=closed]:slide-out-to-right-full',
        'data-[state=open]:slide-in-from-top-full data-[state=open]:sm:slide-in-from-bottom-full',
        tone.container,
        className
      )}
    >
      <div className="p-4">
        <div className="flex items-center gap-3">
          <div className="flex-shrink-0 leading-none">
            <span className="text-lg">{toastIconMap[toast.type]}</span>
          </div>
          <div className="flex flex-1 flex-col gap-2 overflow-hidden">
            <ToastPrimitive.Title className="text-sm font-medium overflow-hidden line-clamp-3">
              {toast.message}
            </ToastPrimitive.Title>
            {toast.action && (
              <button
                type="button"
                onClick={toast.action.onClick}
                className={cn(
                  'inline-flex w-max items-center justify-center rounded-md px-3 py-1 text-xs font-semibold transition focus:outline-hidden focus:ring-2 focus:ring-offset-2 cursor-pointer',
                  tone.action,
                )}
                data-testid={toast.action.testId ?? `app-shell.toast.action.${toast.action.id}`}
              >
                {toast.action.label}
              </button>
            )}
          </div>
          <ToastPrimitive.Close
            className="inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md focus:outline-hidden focus:ring-2 focus:ring-offset-2 hover:opacity-75 cursor-pointer"
          >
            <span className="sr-only">Close</span>
            <ClearButtonIcon className="w-4 h-4" />
          </ToastPrimitive.Close>
        </div>
      </div>
    </ToastPrimitive.Root>
  )
})
ToastComponent.displayName = "ToastComponent"

interface ToastContainerProps {
  toasts: Toast[]
  onRemove: (id: string) => void
  viewportProps?: NativeToastViewportProps & Record<string, unknown>
  getItemProps?: (toast: Toast) => (Partial<ToastProps> & Record<string, unknown>)
}

export function ToastContainer({ toasts, onRemove, viewportProps, getItemProps }: ToastContainerProps) {
  const viewportRecord = (viewportProps ?? {}) as Record<string, unknown>
  const viewportTestId = (viewportRecord['data-testid'] as string | undefined) ?? 'app-shell.toast.viewport'

  return (
    <ToastPrimitive.Provider swipeDirection="right">
      {toasts.map((toast) => {
        const userItemProps = getItemProps?.(toast) || {}
        const resolvedItemProps: Partial<ToastProps> & Record<string, unknown> = {
          'data-toast-id': toast.id,
          'data-toast-level': toast.type,
          ...userItemProps,
        }

        if (resolvedItemProps['data-testid'] === undefined) {
          resolvedItemProps['data-testid'] = 'app-shell.toast.item'
        }

        return (
          <ToastComponent
            key={toast.id}
            toast={toast}
            onRemove={onRemove}
            {...resolvedItemProps}
          />
        )
      })}
      <ToastPrimitive.Viewport
        data-testid={viewportTestId}
        {...viewportProps}
        className={cn(
          "fixed bottom-0 right-0 z-[100] flex max-h-screen w-full flex-col-reverse gap-3 p-4 sm:bottom-0 sm:right-0 sm:top-auto sm:flex-col md:max-w-[420px]",
          viewportProps?.className
        )}
      />
    </ToastPrimitive.Provider>
  )
}
