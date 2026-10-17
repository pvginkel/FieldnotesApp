#!/bin/bash

set -euo pipefail

# Run the testing server directly (no daemonization)
# Playwright starts one per worker; it can also be run directly.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"

echo $BACKEND_DIR

# Port the testing server falls back to when --port is not given. Playwright
# always passes an explicit per-worker port; this only covers manual runs.
TESTING_BACKEND_PORT=3411

# Change to backend directory
cd "$BACKEND_DIR" || {
    echo "Error: Could not change to backend directory: $BACKEND_DIR" >&2
    exit 1
}

# Default server configuration
HOST="0.0.0.0"
PORT="$TESTING_BACKEND_PORT"
DATABASE_URL_OVERRIDE=""

print_usage() {
    cat <<'EOF'
Usage: testing-server.sh [--database-url URL] [--host HOST] [--port PORT]

  --database-url URL   Database to serve
  --host HOST          Host to bind to (default: 0.0.0.0)
  --port PORT          Port to bind to
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --database-url)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --database-url requires a value." >&2
                print_usage
                exit 1
            fi
            DATABASE_URL_OVERRIDE="$2"
            shift 2
            ;;
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

# The settings come from .env, then .env.test over it, as in pytest
# (tests/conftest_infrastructure.py). The app reads .env itself; .env.test is
# exported here so that it wins.
if [[ -f .env.test ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env.test
    set +a
    echo "Loaded test overrides from .env.test"
fi

# The command line wins over both files.
if [[ -n "$DATABASE_URL_OVERRIDE" ]]; then
    export DATABASE_URL="$DATABASE_URL_OVERRIDE"
    echo "Using the database given by --database-url"
    echo
fi

# Mark the process as running in testing mode
export FLASK_ENV=testing

# Run the server through the project's dev entry point (run.py), so it gets
# the same lifecycle coordinator and logging as dev and production.
echo
echo "Starting backend in testing mode..."
echo "Server will run on http://$HOST:$PORT"
echo "Press Ctrl+C to stop"
echo

exec env HOST="$HOST" PORT="$PORT" uv run dev
