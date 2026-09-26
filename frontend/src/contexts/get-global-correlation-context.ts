import { getStoredCorrelationContext } from './correlation-context-base';
import type { CorrelationContextValue } from './correlation-context-base';

export function getGlobalCorrelationContext(): CorrelationContextValue | null {
  return getStoredCorrelationContext();
}
