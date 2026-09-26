import { parseApiError } from '@/lib/utils/error-parsing';

/**
 * Wrap API error payloads in a proper Error subclass so unhandled rejections
 * surface meaningful messages and preserve structured details.
 */
export class ApiError extends Error {
  status?: number;
  details?: unknown;
  raw: unknown;

  constructor(raw: unknown) {
    super(parseApiError(raw));
    this.name = 'ApiError';
    this.raw = raw;

    if (typeof raw === 'object' && raw !== null) {
      const maybeObject = raw as Record<string, unknown>;

      if (typeof maybeObject.status === 'number') {
        this.status = maybeObject.status;
      }

      if ('details' in maybeObject) {
        this.details = maybeObject.details;
      }
    }

    // Preserve the original payload for debugging
    this.cause = raw;

    if (Error.captureStackTrace) {
      Error.captureStackTrace(this, ApiError);
    }
  }
}

/**
 * Convert an unknown error payload into a proper Error instance.
 * When a numeric `status` is provided (typically from `response.status`),
 * it is attached to the resulting error for downstream consumers like
 * `isUnauthorizedError()`.
 */
export function toApiError(error: unknown, status?: number): Error {
  if (error instanceof Error) {
    // If we have a status, attach it to the existing error
    if (status !== undefined) {
      (error as Error & { status?: number }).status = status;
    }
    return error;
  }
  const apiError = new ApiError(error);
  // Override status from response if provided (more reliable than parsing body)
  if (status !== undefined) {
    apiError.status = status;
  }
  return apiError;
}

/**
 * Type-safe predicate for detecting 401 Unauthorized errors.
 * Used by useAuth to distinguish "not logged in" from real failures.
 */
export function isUnauthorizedError(error: unknown): boolean {
  if (error instanceof ApiError && error.status === 401) {
    return true;
  }
  // Also check for raw error objects that might have status
  if (typeof error === 'object' && error !== null && 'status' in error) {
    return (error as { status: unknown }).status === 401;
  }
  return false;
}

/**
 * Type-safe predicate for detecting 403 Forbidden errors.
 * Used by useAuth to distinguish "authenticated but no app roles" from other failures.
 */
export function isForbiddenError(error: unknown): boolean {
  if (error instanceof ApiError && error.status === 403) return true;
  if (typeof error === 'object' && error !== null && 'status' in error)
    return (error as { status: unknown }).status === 403;
  return false;
}
