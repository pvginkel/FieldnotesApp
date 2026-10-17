#!/usr/bin/env bash
# Development runner: runs a command, `uv run dev` by default, and restarts it
# when a file it depends on changes, or when it exits.
#
#   scripts/dev-watch.sh                # the backend dev server
#   scripts/dev-watch.sh uv run <cmd>   # any other long-running command
#
# watchmedo watches the backend's code (*.py, outside tests/ and alembic/),
# its .env files and app/assets/. On a change it sends SIGTERM to the
# command's process group; the lifecycle coordinator turns that into a
# graceful shutdown, and watchmedo starts the command again once the wrapper
# below has exited. The wrapper outlives the SIGTERM (its trap runs only after
# the command has exited), so the old server has let go of its port before the
# new one starts; watchmedo sends SIGKILL after 10 s. Changes within a second
# of each other (a checkout, a save-all) make one restart. When the command
# exits by itself, the wrapper waits 3 s before watchmedo restarts it, so a
# server that fails at startup does not restart in a tight loop. run.py runs
# without the Werkzeug reloader, so this is the one process watching files.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
    set -- uv run dev
fi

# watchdog matches a pattern like PurePath.match: from the right, and each *
# is one path segment. So the watched directory is absolute (a relative one
# loses its leading segment), and a directory's contents take one pattern per
# depth.
exec uv run -- watchmedo auto-restart \
    --directory="$PWD" \
    --recursive \
    --patterns='*.py;.env*;*/app/assets/*;*/app/assets/*/*' \
    --ignore-patterns='*/tests/*;*/tests/*/*;*/tests/*/*/*;*/alembic/*;*/alembic/*/*' \
    --signal SIGTERM \
    --debounce-interval 1 \
    -- bash -c '
        trap "restarting=1" TERM
        "$@"
        if [ -z "${restarting:-}" ]; then
            echo
            echo "Process exited. Restarting in 3 seconds..."
            echo
            sleep 3
        fi
    ' _ "$@"
