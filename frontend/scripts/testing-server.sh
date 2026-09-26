#!/bin/bash

# Frontend testing server script for Playwright integration.
# Runs the Vite dev server per worker with isolated optimize cache directories.

set -euo pipefail

HOST="127.0.0.1"
PORT="3100"

print_usage() {
  cat <<'EOF'
Usage: testing-server.sh [--host HOST] [--port PORT]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      if [[ -z "${2:-}" ]]; then
        echo "Error: --host requires a value." >&2
        print_usage
        exit 1
      fi
      HOST="$2"
      shift 2
      ;;
    --port)
      if [[ -z "${2:-}" ]]; then
        echo "Error: --port requires a value." >&2
        print_usage
        exit 1
      fi
      PORT="$2"
      shift 2
      ;;
    --help|-h)
      print_usage
      exit 0
      ;;
    *)
      echo "Error: Unknown argument: $1" >&2
      print_usage
      exit 1
      ;;
  esac
done

echo "Starting frontend testing server..." >&2
echo "Setting VITE_TEST_MODE=true" >&2
echo "Host: ${HOST}" >&2
echo "Port: ${PORT}" >&2

# Ensure the dev server proxies API calls to the Playwright backend.
if [ -z "${BACKEND_URL:-}" ]; then
  export BACKEND_URL=http://localhost:5100
  echo "Using default BACKEND_URL=${BACKEND_URL}" >&2
else
  echo "Using BACKEND_URL=${BACKEND_URL}" >&2
fi

cache_root="node_modules/.tmp/vite-playwright"
cache_dir="${cache_root}-${PORT}"

mkdir -p "${cache_root}"

export VITE_TEST_MODE=true
export NODE_ENV=test
export VITE_CACHE_DIR="${cache_dir}"

exec pnpm exec vite --host "${HOST}" --port "${PORT}" --strictPort --mode test
