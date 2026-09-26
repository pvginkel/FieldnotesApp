/**
 * SSE provider group.
 * Wraps SseContextProvider and DeploymentProvider.
 * Requires auth to be resolved before SSE connections are established.
 */

import type { ReactNode } from 'react';
import { SseContextProvider } from '@/contexts/sse-context-provider';
import { SseGate } from '@/components/sse/sse-gate';
import { DeploymentProvider } from '@/contexts/deployment-context';

export function SseProviders({ children }: { children: ReactNode }) {
  return (
    <SseContextProvider>
      <SseGate>
        <DeploymentProvider>{children}</DeploymentProvider>
      </SseGate>
    </SseContextProvider>
  );
}
