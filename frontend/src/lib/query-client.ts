import { QueryClient } from '@tanstack/react-query'
import { parseApiError, is404Error } from '@/lib/utils/error-parsing'
import { setupQueryInstrumentation } from '@/lib/test/query-instrumentation'

// Store the toast function reference to avoid circular dependencies
let toastFunction: ((message: string, error?: unknown) => void) | null = null

export function setToastFunction(handler: (message: string, error?: unknown) => void) {
  toastFunction = handler
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        // Don't retry on 404s
        if (is404Error(error)) {
          return false
        }

        // Don't retry on client errors (4xx)
        if (error instanceof Error && 'status' in error) {
          const status = (error as Error & { status: number }).status
          if (status >= 400 && status < 500) {
            return false
          }
        }

        // Retry up to 3 times for server errors and network issues
        return failureCount < 3
      },
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000),
      staleTime: 5 * 60 * 1000, // 5 minutes
    },
    mutations: {
      retry: false, // Don't retry mutations by default
      onError: (error: any) => {
        // Always show mutation errors to the user
        if (toastFunction) {
          const message = parseApiError(error)
          toastFunction(message, error)
        }
      }
    }
  }
})

// Setup query instrumentation in test mode
setupQueryInstrumentation(queryClient)
