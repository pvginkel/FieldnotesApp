/**
 * Core provider group.
 * Wraps QueryClientProvider, ToastProvider, and QuerySetup.
 *
 * QuerySetup wires the toast function to the query client so mutation errors
 * surface as toast notifications. It must render inside ToastProvider.
 */

import { useEffect, type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '@/contexts/toast-context';
import { queryClient, setToastFunction } from '@/lib/query-client';
import { useToast } from '@/hooks/use-toast';

function QuerySetup({ children }: { children: ReactNode }) {
  const { showError, showException } = useToast();

  useEffect(() => {
    setToastFunction((message, error) => {
      if (error !== undefined) {
        showException(message, error);
        return;
      }
      showError(message);
    });
  }, [showError, showException]);

  return <>{children}</>;
}

export function CoreProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <QuerySetup>{children}</QuerySetup>
      </ToastProvider>
    </QueryClientProvider>
  );
}
