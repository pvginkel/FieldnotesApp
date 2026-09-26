#!/usr/bin/env bash
# Start the SSE gateway sidecar for local development (honcho `gateway` service).
#
# The gateway is the `ssegateway` package (github:pvginkel/SSEGateway#stable, a
# devDependency of the frontend) — the same package the Playwright harness runs
# (frontend/tests/support/process/servers.ts) and the same one the production
# deployment runs as a sidecar. No sibling checkout is needed: cd into frontend/
# so Node resolves it from frontend/node_modules, then exec it directly (clean
# under honcho's piped stdin).
set -euo pipefail

export PORT="${SSE_GATEWAY_PORT:-3402}"
export CALLBACK_URL="${CALLBACK_URL:-http://localhost:3401/api/sse/callback}"

cd "$(dirname "$0")/../frontend"

exec node -e "require(require.resolve('ssegateway'))"
