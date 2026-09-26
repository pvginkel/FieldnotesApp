/**
 * Auth provider group.
 * Wraps AuthProvider and AuthGate.
 * AuthGate blocks child rendering until authentication state resolves.
 */

import type { ReactNode } from 'react';
import { AuthProvider } from '@/contexts/auth-context';
import { AuthGate } from '@/components/auth/auth-gate';

export function AuthProviders({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AuthGate>{children}</AuthGate>
    </AuthProvider>
  );
}
