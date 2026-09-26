import { isTestMode } from '@/lib/config/test-mode';
import { makeUniqueToken } from '@/lib/utils/random';

let cachedRequestId: string | undefined;

/**
 * Get a unique request ID for the deployment SSE connection.
 *
 * Generates a fresh ID on first call within each page load. This ensures each
 * tab (including duplicated tabs) gets its own unique ID, preventing multiple
 * tabs from fighting over a single SSE connection slot in the backend.
 */
export function getDeploymentRequestId(): string {
  if (cachedRequestId) {
    return cachedRequestId;
  }

  cachedRequestId = makeUniqueToken(32);
  return cachedRequestId;
}

export function resetDeploymentRequestId(): void {
  if (!isTestMode()) {
    return;
  }

  cachedRequestId = undefined;
}

function registerTestBridge(): void {
  if (!isTestMode()) {
    return;
  }

  if (typeof window === 'undefined') {
    return;
  }

  const globalWindow = window as typeof window & {
    __resetDeploymentRequestId?: () => void;
  };

  if (!globalWindow.__resetDeploymentRequestId) {
    globalWindow.__resetDeploymentRequestId = () => {
      resetDeploymentRequestId();
    };
  }
}

registerTestBridge();
