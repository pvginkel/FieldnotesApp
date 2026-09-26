#!/usr/bin/env python3
"""Start all dev services via honcho (process manager).

Usage:
    ./scripts/dev.py                    # start all services (backend + frontend + gateway)
    ./scripts/dev.py backend frontend   # start a subset (positional; honcho's -e is --env, not --except)

Reads the repo-root Procfile.dev. Per-service logs (ANSI-stripped) are written
to logs/<service>.log. Ctrl-C stops everything cleanly.

honcho runs inside the modern-app tool container: the dev container has neither
poetry nor honcho, and every service needs that container anyway, so the
Procfile lines run there natively without their own cexec. Terminating the
cexec client stops the processes in the sidecar with it.

This wrapper still runs honcho under a PID namespace (unshare --user --pid
--fork) so nothing local is left behind.

Note: run `kc project setup` first — it installs the poetry and pnpm
dependencies the services need (the SSE gateway on :3402 runs the
`ssegateway` frontend devDependency).
"""

import io
import os
import pty
import re
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"

ANSI_RE = re.compile(rb"\x1b\[[0-9;]*[a-zA-Z]")

# Honcho prefixes lines like: '19:05:11 backend.1  | ...'
# Extract the service name to route to per-service log files.
PREFIX_RE = re.compile(rb"^\d{2}:\d{2}:\d{2}\s+(\S+)\s+\|")

log_files: dict[bytes, "io.BufferedWriter"] = {}
buf = b""


def get_log(service: bytes) -> "io.BufferedWriter":
    if service not in log_files:
        name = service.rsplit(b".", 1)[0]  # 'backend.1' -> 'backend'
        log_files[service] = open(LOGS / (name.decode() + ".log"), "wb")
    return log_files[service]


def read(fd: int) -> bytes:
    global buf
    data = os.read(fd, 4096)
    buf += data

    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        stripped = ANSI_RE.sub(b"", line)
        m = PREFIX_RE.match(stripped)
        if m:
            log = get_log(m.group(1))
            # Strip the honcho prefix, keep only the service's own output
            content = stripped[stripped.index(b"| ") + 2 :]
            log.write(content + b"\n")
            log.flush()

    return data


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    os.chdir(ROOT)

    # Force colors in subprocesses — honcho pipes their output, so they
    # don't see a TTY. These env vars re-enable colors for common tools.
    os.environ["FORCE_COLOR"] = "1"  # Node.js / chalk / Vite
    os.environ["PY_COLORS"] = "1"  # Python tools that check this

    # Ignore signals in this process — let honcho handle them.
    # When honcho exits, the PID namespace ensures all children are killed.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    # Run honcho inside a PTY (for colors) and a PID namespace (for cleanup).
    status = pty.spawn(
        ["unshare", "--user", "--pid", "--fork",
         "cexec", "modern-app",
         "poetry", "run", "honcho", "start", "-f", "Procfile.dev"] + sys.argv[1:],
        read,
    )

    for f in log_files.values():
        f.close()

    exit_code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
