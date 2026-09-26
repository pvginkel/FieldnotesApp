/**
 * Custom hook for authentication state management.
 * Wraps useGetAuthSelf and provides 401 detection for redirect logic.
 */

import { useGetAuthSelf, type UserInfoResponseSchema_a535b8c } from '@/lib/api/generated/hooks';
import { isForbiddenError, isUnauthorizedError } from '@/lib/api/api-error';

/**
 * Frontend user info model.
 * Fields map directly from the API response (already camelCase-compatible).
 */
export interface UserInfo {
  email: string | null;
  name: string | null;
  roles: string[];
  subject: string;
}

/**
 * Result type for the useAuth hook.
 */
export interface UseAuthResult {
  user: UserInfo | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  isUnauthenticated: boolean;
  /** True when /api/auth/self returns 403 — user is authenticated but has no app roles. */
  isForbidden: boolean;
  error: Error | null;
  refetch: () => void;
}

/**
 * Transform API response to frontend model.
 * The API already returns fields in a compatible format, but we
 * maintain this layer for consistency with other hooks.
 */
function transformUserInfo(data: UserInfoResponseSchema_a535b8c): UserInfo {
  return {
    email: data.email,
    name: data.name,
    roles: data.roles,
    subject: data.subject,
  };
}

/**
 * Hook for fetching and managing authentication state.
 *
 * Key behaviors:
 * - Fetches user info from /api/auth/self on mount
 * - Distinguishes between 401 (unauthenticated) and other errors
 * - Does not auto-redirect; the client middleware handles 401 redirects
 * - Disables automatic retries for auth check (we handle manually)
 */
export function useAuth(): UseAuthResult {
  const query = useGetAuthSelf(undefined, {
    // Disable automatic retry for auth - we handle 401 specially
    retry: false,
    // Keep stale data during refetch to prevent flickering
    staleTime: Infinity,
    // Don't refetch on window focus - auth state is stable
    refetchOnWindowFocus: false,
  });

  const { data, error, isLoading, refetch } = query;

  // Determine if we have a 401 (unauthenticated) or 403 (forbidden) vs a real error
  const is401 = error ? isUnauthorizedError(error) : false;
  const is403 = error ? isForbiddenError(error) : false;

  // User is authenticated if we have data
  const user = data ? transformUserInfo(data) : null;
  const isAuthenticated = user !== null;

  // User is unauthenticated if we got a 401 (not loading, explicit 401)
  const isUnauthenticated = !isLoading && is401;

  // User is authenticated but has no app roles if we got a 403
  const isForbidden = !isLoading && is403;

  // Only surface errors that are neither 401 nor 403 as actual failures
  const effectiveError = error && !is401 && !is403 ? (error as Error) : null;

  return {
    user,
    isLoading,
    isAuthenticated,
    isUnauthenticated,
    isForbidden,
    error: effectiveError,
    refetch: () => void refetch(),
  };
}
