/**
 * Shared authentication redirect utilities.
 * Used by both auth-context and API middleware for consistent 401 handling.
 */

/**
 * Build the login redirect URL with the current path preserved.
 * Encodes the current pathname and search params so the user can be
 * redirected back after authentication.
 */
export function buildLoginUrl(): string {
  const currentPath = window.location.pathname + window.location.search;
  const encodedRedirect = encodeURIComponent(currentPath);
  return `/api/auth/login?redirect=${encodedRedirect}`;
}
