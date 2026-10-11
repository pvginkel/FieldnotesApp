// Every API request names the page's SSE connection in this header, once the page has one, so the
// API can bind that connection to whoever the request authenticated as (NFR-4).

import { api } from '@/lib/api/client';

const HEADER = 'X-SSE-Request-Id';

let named: string | null = null;

api.use({
  onRequest({ request }) {
    if (named) request.headers.set(HEADER, named);
    return request;
  },
});

/** Names `requestId`, the page's SSE connection, in every API request from now on. */
export function nameSseConnection(requestId: string): void {
  named = requestId;
}
