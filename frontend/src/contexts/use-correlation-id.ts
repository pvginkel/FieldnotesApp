import { useContext } from 'react';
import { CorrelationContext } from './correlation-context-base';

export function useCorrelationId() {
  const context = useContext(CorrelationContext);
  if (!context) {
    throw new Error('useCorrelationId must be used within a CorrelationProvider');
  }
  return context;
}
