/**
 * Composed provider tree.
 * Nests active provider groups based on enabled features.
 *
 * Provider ordering:
 * 1. CoreProviders: QueryClientProvider > ToastProvider > QuerySetup (always)
 * 2. AuthProviders: AuthProvider > AuthGate (use_oidc)
 * 3. SseProviders: SseContextProvider > DeploymentProvider (use_sse)
 */

import type { ReactNode } from 'react';
import { CoreProviders } from './core-providers';
import { AuthProviders } from './auth-providers';
import { SseProviders } from './sse-providers';

export function Providers({ children }: { children: ReactNode }) {
  return (
    <CoreProviders>
      <AuthProviders>
        <SseProviders>
          {children}
        </SseProviders>
      </AuthProviders>
    </CoreProviders>
  );
}
