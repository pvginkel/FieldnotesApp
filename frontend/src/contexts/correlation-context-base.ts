import { createContext } from 'react';

export interface CorrelationContextValue {
  setCorrelationId: (id: string) => void;
  getCorrelationId: () => string | undefined;
}

export const CorrelationContext = createContext<CorrelationContextValue | null>(null);

let currentGlobalContext: CorrelationContextValue | null = null;

export function setGlobalCorrelationContext(value: CorrelationContextValue | null) {
  currentGlobalContext = value;
}

export function getStoredCorrelationContext(): CorrelationContextValue | null {
  return currentGlobalContext;
}
