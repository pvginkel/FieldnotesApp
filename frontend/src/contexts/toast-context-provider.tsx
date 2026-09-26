import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ToastContainer } from '@/components/primitives/toast'
import type { Toast, ToastOptions, ToastType } from '@/components/primitives/toast'
import { isTestMode } from '@/lib/config/test-mode'
import { createInstrumentedToastWrapper } from '@/lib/test/toast-instrumentation'
import { ToastContext } from './toast-context-base'
import type { ToastContextValue } from './toast-context-base'

interface ToastProviderProps {
  children: ReactNode
}

const DEFAULT_TOAST_DURATION_MS = 15000

export function ToastProvider({ children }: ToastProviderProps) {
  const [toasts, setToasts] = useState<Toast[]>([])
  // Track timeout IDs for custom auto-dismiss management (workaround for Radix UI timer bugs)
  const timeoutRefs = useRef<Map<string, NodeJS.Timeout>>(new Map())
  // Use counter for toast IDs to avoid Math.random() during render
  const idCounterRef = useRef(0)

  const removeToast = (id: string) => {
    setToasts(prev => prev.filter(toast => toast.id !== id))
    // Clear timeout when toast is manually removed
    const timeout = timeoutRefs.current.get(id)
    if (timeout) {
      clearTimeout(timeout)
      timeoutRefs.current.delete(id)
    }
  }

  const showToast = (message: string, type: ToastType, options?: ToastOptions) => {
    const id = `toast-${++idCounterRef.current}`
    const action = options?.action
      ? {
          ...options.action,
          onClick: () => {
            options.action?.onClick?.()
            removeToast(id)
          },
        }
      : undefined

    const toast: Toast = {
      id,
      message,
      type,
      duration: options?.duration,
      action,
    }

    setToasts(prev => [...prev, toast])

    // Custom timeout management to work around Radix UI timer bugs
    // Force dismissal after duration regardless of user interaction
    const duration = options?.duration ?? DEFAULT_TOAST_DURATION_MS
    const timeout = setTimeout(() => {
      removeToast(id)
      timeoutRefs.current.delete(id)
    }, duration)
    timeoutRefs.current.set(id, timeout)

    return id
  }

  const showError = (message: string, options?: ToastOptions) => {
    return showToast(message, 'error', options)
  }

  const showSuccess = (message: string, options?: ToastOptions) => {
    return showToast(message, 'success', options)
  }

  const showWarning = (message: string, options?: ToastOptions) => {
    return showToast(message, 'warning', options)
  }

  const showInfo = (message: string, options?: ToastOptions) => {
    return showToast(message, 'info', options)
  }

  const showException = (message: string, error: unknown, options?: ToastOptions) => {
    if (process.env.NODE_ENV !== 'production') {
      console.error('Toast exception', error)
    }
    return showToast(message, 'error', options)
  }

  const baseContextValue: ToastContextValue = {
    showToast,
    showError,
    showSuccess,
    showWarning,
    showInfo,
    showException,
    removeToast
  }

  // Instrument toast functions in test mode
  const contextValue = isTestMode()
    // eslint-disable-next-line react-hooks/refs -- baseContextValue is not a ref, false positive
    ? createInstrumentedToastWrapper(baseContextValue)
    : baseContextValue

  const showExceptionFn = contextValue.showException

  useEffect(() => {
    if (!isTestMode() || typeof window === 'undefined') {
      return
    }

    const handler = (event: Event) => {
      const customEvent = event as CustomEvent<{ message: string; error: unknown; options?: ToastOptions }>
      const detail = customEvent.detail
      if (!detail || typeof detail.message !== 'string') {
        return
      }
      showExceptionFn(detail.message, detail.error, detail.options)
    }

    window.addEventListener('app:testing:show-exception', handler)

    return () => {
      window.removeEventListener('app:testing:show-exception', handler)
    }
  }, [showExceptionFn])

  // Cleanup timeouts on unmount
  useEffect(() => {
    const timeouts = timeoutRefs.current
    return () => {
      timeouts.forEach((timeout) => {
        clearTimeout(timeout)
      })
      timeouts.clear()
    }
  }, [])

  return (
    <ToastContext.Provider value={contextValue}>
      {children}
      <ToastContainer
        toasts={toasts}
        onRemove={removeToast}
      />
    </ToastContext.Provider>
  )
}
