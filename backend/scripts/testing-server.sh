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
SQLITE_DB_PATH=""

print_usage() {
    cat <<'EOF'
Usage: testing-server.sh [--sqlite-db PATH] [--host HOST] [--port PORT]
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sqlite-db)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --sqlite-db requires a value." >&2
                print_usage
                exit 1
            fi
            SQLITE_DB_PATH="$2"
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

if [[ -n "$SQLITE_DB_PATH" ]]; then
    export DATABASE_URL="sqlite:////${SQLITE_DB_PATH}"
    echo "Using SQLite database at $SQLITE_DB_PATH"
    echo
fi

# Mark the process as running in testing mode
export FLASK_ENV=testing

# Run the Flask server
echo
echo "Starting backend in testing mode..."
echo "Server will run on http://$HOST:$PORT"
echo "Press Ctrl+C to stop"
echo

exec uv run python -m flask run --host="$HOST" --port="$PORT"
