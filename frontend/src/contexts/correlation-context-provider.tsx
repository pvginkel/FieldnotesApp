/**
 * Correlation ID context for linking API requests with errors
 * Uses ref-based storage to avoid re-renders while maintaining correlation IDs
 */

import { useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react';
import {
  CorrelationContext,
  setGlobalCorrelationContext,
} from './correlation-context-base';
import type { CorrelationContextValue } from './correlation-context-base';

interface CorrelationProviderProps {
  children: ReactNode;
}

export function CorrelationProvider({ children }: CorrelationProviderProps) {
  // Use ref to store correlation ID without causing re-renders
  const correlationIdRef = useRef<string | undefined>(undefined);

  // Stable function references using useCallback
  const setCorrelationId = useCallback((id: string) => {
    correlationIdRef.current = id;
  }, []);

  const getCorrelationId = useCallback(() => {
    return correlationIdRef.current;
  }, []);

  // Context value contains only stable function references
  const contextValue: CorrelationContextValue = useMemo(() => ({
    setCorrelationId,
    getCorrelationId,
  }), [setCorrelationId, getCorrelationId]);

  // Store reference globally for non-React usage
  useEffect(() => {
    setGlobalCorrelationContext(contextValue);
    return () => {
      setGlobalCorrelationContext(null);
    };
  }, [contextValue]);

  return (
    <CorrelationContext.Provider value={contextValue}>
      {children}
    </CorrelationContext.Provider>
  );
}
