/* eslint-disable react-refresh/only-export-components */
/**
 * Authentication context and provider.
 * Provides auth state to the component tree with test instrumentation.
 *
 * Note: The openapi-fetch middleware in client.ts is the SOLE authority for
 * 401-to-login redirects. This provider does NOT redirect on 401 --
 * it only surfaces auth state for the AuthGate and other consumers.
 */

import { createContext, useContext, useEffect, type ReactNode } from 'react';
import { useAuth, type UserInfo } from '@/hooks/use-auth';
import { isTestMode } from '@/lib/config/test-mode';
import { emitTestEvent } from '@/lib/test/event-emitter';
import { TestEventKind, type UiStateTestEvent } from '@/lib/test/test-events';

/**
 * Auth context value shape.
 */
export interface AuthContextValue {
  user: UserInfo | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** True when /api/auth/self returns 403 — user is authenticated but has no app roles. */
  isForbidden: boolean;
  error: Error | null;
  logout: () => void;
  refetch: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Hook to access auth context.
 * Must be used within AuthProvider.
 */
export function useAuthContext(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuthContext must be used within AuthProvider');
  }
  return context;
}

interface AuthProviderProps {
  children: ReactNode;
}

/**
 * Perform logout by navigating to the logout endpoint.
 */
function performLogout(): void {
  window.location.href = '/api/auth/logout';
}

/**
 * AuthProvider component.
 * Fetches auth state on mount and provides it via context.
 * Does NOT handle 401 redirects -- that is the middleware's job.
 */
export function AuthProvider({ children }: AuthProviderProps) {
  const { user, isLoading, isAuthenticated, isForbidden, error, refetch } = useAuth();

  // Emit auth state events for test instrumentation
  useEffect(() => {
    if (!isTestMode()) return;

    if (isLoading) {
      const loadingPayload: Omit<UiStateTestEvent, 'timestamp'> = {
        kind: TestEventKind.UI_STATE,
        scope: 'auth',
        phase: 'loading',
      };
      emitTestEvent(loadingPayload);
    } else if (isAuthenticated && user) {
      const readyPayload: Omit<UiStateTestEvent, 'timestamp'> = {
        kind: TestEventKind.UI_STATE,
        scope: 'auth',
        phase: 'ready',
        metadata: { userId: user.subject },
      };
      emitTestEvent(readyPayload);
    } else if (isForbidden) {
      const forbiddenPayload: Omit<UiStateTestEvent, 'timestamp'> = {
        kind: TestEventKind.UI_STATE,
        scope: 'auth',
        phase: 'forbidden',
      };
      emitTestEvent(forbiddenPayload);
    } else if (error) {
      const errorPayload: Omit<UiStateTestEvent, 'timestamp'> = {
        kind: TestEventKind.UI_STATE,
        scope: 'auth',
        phase: 'error',
        metadata: { message: error.message },
      };
      emitTestEvent(errorPayload);
    }
  }, [isLoading, isAuthenticated, user, isForbidden, error]);

  const contextValue: AuthContextValue = {
    user,
    isLoading,
    isAuthenticated,
    isForbidden,
    error,
    logout: performLogout,
    refetch,
  };

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
}
