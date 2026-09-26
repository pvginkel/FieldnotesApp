import { useState, useEffect } from 'react';
import { useSseContext } from '@/contexts/sse-context';

/**
 * Version event data as received from the SSE stream
 */
interface VersionEventData {
  version: string;
  correlation_id?: string;
  correlationId?: string;
  request_id?: string;
  requestId?: string;
}

interface UseVersionSSEReturn {
  isConnected: boolean;
  version: string | null;
}

/**
 * Hook to consume version events from the unified SSE stream.
 *
 * Subscribes to 'version' events via addEventListener and parses
 * version data locally. Connection lifecycle is managed by the provider.
 */
export function useVersionSSE(): UseVersionSSEReturn {
  const [version, setVersion] = useState<string | null>(null);
  const { isConnected, addEventListener } = useSseContext();

  useEffect(() => {
    const unsubscribe = addEventListener('version', (data: unknown) => {
      const event = data as VersionEventData;
      if (event.version) {
        setVersion(event.version);
      }
    });

    return unsubscribe;
  }, [addEventListener]);

  return {
    isConnected,
    version,
  };
}
