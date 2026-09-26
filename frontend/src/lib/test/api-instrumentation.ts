/**
 * API client instrumentation for test events
 * Adds request/response interceptors to emit API test-event payloads
 */

import type { Client } from 'openapi-fetch';
import { ulid } from 'ulid';
import { emitTestEvent } from './event-emitter';
import { TestEventKind, type ApiTestEvent } from '@/lib/test/test-events';
import { getGlobalCorrelationContext } from '@/contexts/correlation-context';

// Store request timing information
const requestTiming = new Map<string, number>();

/**
 * Generate a correlation ID for tracking requests
 * Checks context for existing ID or generates a new one
 */
function getOrGenerateCorrelationId(): string {
  const context = getGlobalCorrelationContext();
  const existingId = context?.getCorrelationId();

  if (existingId) {
    return existingId;
  }

  // Generate new correlation ID
  const newId = ulid();

  // Store in context if available
  context?.setCorrelationId(newId);

  return newId;
}

/**
 * Extract operation name from URL path and method
 */
function extractOperationName(url: string, method: string): string {
  try {
    const urlObj = new URL(url);
    const pathSegments = urlObj.pathname.split('/').filter(Boolean);
    const basePath = pathSegments.join('_');
    return `${method.toLowerCase()}_${basePath}`;
  } catch {
    return `${method.toLowerCase()}_unknown`;
  }
}

/**
 * Setup API instrumentation to emit API test-event payloads
 */
export function setupApiInstrumentation(client: Client<any>): void {
  // Request interceptor
  client.use({
    onRequest({ request }) {
      const correlationId = getOrGenerateCorrelationId();

      // Add correlation ID header
      request.headers.set('X-Request-Id', correlationId);

      // Store start time for duration calculation
      requestTiming.set(correlationId, performance.now());

      return request;
    },

    onResponse({ request, response }) {
      const correlationId = request.headers.get('X-Request-Id') || 'unknown';
      const startTime = requestTiming.get(correlationId);
      const durationMs = startTime ? Math.round(performance.now() - startTime) : 0;

      // Clean up timing data
      if (startTime) {
        requestTiming.delete(correlationId);
      }

      // Extract operation name from request
      const operation = extractOperationName(request.url, request.method);

      // Emit API test event
      const apiEvent: Omit<ApiTestEvent, 'timestamp'> = {
        kind: TestEventKind.API,
        operation,
        method: request.method,
        status: response.status,
        correlationId,
        durationMs,
      };

      emitTestEvent(apiEvent);

      // For error responses, try to attach correlation ID to the response
      // This helps query-instrumentation.ts pick it up later
      if (!response.ok && response.status >= 400) {
        // Try to attach correlation ID to response for downstream error handling
        try {
          // Store correlation ID in a way that error handlers can access
          (response as any).correlationId = correlationId;
        } catch {
          // Ignore if we can't attach
        }
      }

      return response;
    }
  });
}
