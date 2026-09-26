/**
 * Inline error type definitions — no dependency on generated types.
 * These match the standard backend error response shapes.
 */

interface ErrorDetail {
  field?: string
  message?: string
  [key: string]: unknown
}

interface ValidationErrorElement {
  loc?: (string | number)[]
  msg: string
  type?: string
}

type ValidationError = ValidationErrorElement[]

interface ApiError {
  error: string
  details?: ErrorDetail | ErrorDetail[]
}

interface FetchError extends Error {
  status: number
}

export function parseApiError(error: unknown): string {
  // Handle fetch/network errors
  if (error instanceof Error) {
    if ('status' in error && typeof (error as Error & { status: unknown }).status === 'number') {
      const fetchError = error as FetchError
      if (fetchError.status === 404) {
        return 'Resource not found'
      }
      if (fetchError.status >= 500) {
        return 'Server error occurred. Please try again later.'
      }
    }
    return error.message || 'An unexpected error occurred'
  }

  // Handle structured API error responses
  if (isApiError(error)) {
    return error.error
  }

  // Handle validation errors
  if (isValidationError(error)) {
    return formatValidationErrors(error)
  }

  // Handle error objects with message property
  if (typeof error === 'object' && error !== null && 'message' in error) {
    return String((error as { message: unknown }).message)
  }

  // Handle string errors
  if (typeof error === 'string') {
    return error
  }

  // Fallback for unknown error formats
  return 'An unexpected error occurred'
}

export function formatValidationErrors(errors: ValidationError): string {
  if (!Array.isArray(errors) || errors.length === 0) {
    return 'Validation failed'
  }

  // If there's only one error, return it directly
  if (errors.length === 1) {
    const error = errors[0]
    return error.msg || 'Validation error'
  }

  // For multiple errors, format as a list
  const errorMessages = errors.map(error => error.msg).filter(Boolean)
  
  if (errorMessages.length === 0) {
    return 'Multiple validation errors occurred'
  }

  if (errorMessages.length === 1) {
    return errorMessages[0]
  }

  return `Multiple errors: ${errorMessages.join(', ')}`
}

export function shouldDisplayError(error: unknown): boolean {
  // Don't display 404 errors for optional queries
  if (error instanceof Error && 'status' in error && (error as Error & { status: unknown }).status === 404) {
    return false
  }

  // Display all other errors
  return true
}

function isApiError(error: unknown): error is ApiError {
  return (
    typeof error === 'object' &&
    error !== null &&
    'error' in error &&
    typeof (error as { error: unknown }).error === 'string'
  )
}

function isValidationError(error: unknown): error is ValidationError {
  return (
    Array.isArray(error) &&
    error.length > 0 &&
    typeof error[0] === 'object' &&
    error[0] !== null &&
    'msg' in error[0]
  )
}

export function is404Error(error: unknown): boolean {
  return (
    error instanceof Error &&
    'status' in error &&
    (error as Error & { status: unknown }).status === 404
  )
}