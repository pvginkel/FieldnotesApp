#!/usr/bin/env python3
"""Start all dev services via honcho (process manager).

Usage:
    ./scripts/dev.py                    # start all services (backend + frontend)
    ./scripts/dev.py backend frontend   # start a subset (positional; honcho's -e is --env, not --except)
    ./scripts/dev.py stop               # stop a running stack from another shell

Reads the repo-root Procfile.dev. Per-service logs (ANSI-stripped) are written
to logs/<service>.log. Ctrl-C stops everything cleanly, and so does SIGINT or
SIGTERM sent to this script from another shell (what `stop` does): the signal
is forwarded to honcho as a Ctrl-C through the PTY. The script's pid is in
logs/dev.pid while it runs.

honcho runs inside the modern-app tool container: the dev container has neither
uv nor honcho, and every service needs that container anyway, so the
Procfile lines run there natively without their own cexec. Do not kill the
cexec client to stop the stack: honcho dies of the hangup without stopping its
services, which run in sessions of their own and keep running in the sidecar.

This wrapper still runs honcho under a PID namespace (unshare --user --pid
--fork) so nothing local is left behind.

Note: run `kc project setup` first — it installs the uv and pnpm
dependencies the services need.
"""

import io
import os
import pty
import re
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
PIDFILE = LOGS / "dev.pid"

ANSI_RE = re.compile(rb"\x1b\[[0-9;]*[a-zA-Z]")

# Honcho prefixes lines like: '19:05:11 backend.1  | ...'
# Extract the service name to route to per-service log files.
PREFIX_RE = re.compile(rb"^\d{2}:\d{2}:\d{2}\s+(\S+)\s+\|")
# ... and reports each service as '19:05:11 system  | backend.1 started (pid=42)'.
STARTED_RE = re.compile(rb"^\d{2}:\d{2}:\d{2}\s+system\s+\|\s+(\S+)\.\d+ started")
# Honcho's own Procfile syntax.
PROCFILE_RE = re.compile(r"^([A-Za-z0-9_-]+):")

log_files: dict[bytes, "io.BufferedWriter"] = {}
buf = b""

# The PTY master, known from the first read, and the services honcho has yet
# to report started. Honcho crashes (KeyError: 'pid') on a SIGINT that arrives
# before every service has started, so a stop requested earlier is held until
# then.
master_fd: int | None = None
not_started: set[bytes] = set()
stop_pending = False


def get_log(service: bytes) -> "io.BufferedWriter":
    if service not in log_files:
        name = service.rsplit(b".", 1)[0]  # 'backend.1' -> 'backend'
        log_files[service] = open(LOGS / (name.decode() + ".log"), "wb")
    return log_files[service]


def send_interrupt() -> None:
    global stop_pending
    if master_fd is None or not_started:
        stop_pending = True
    else:
        stop_pending = False
        os.write(master_fd, b"\x03")


def on_signal(signum: int, frame: object) -> None:
    # A terminal Ctrl-C reaches honcho as a byte through the PTY (pty.spawn puts
    # the local terminal in raw mode); a signal from another shell reaches only
    # this process. Turn it into the same byte, so honcho stops every service.
    send_interrupt()


def read(fd: int) -> bytes:
    global buf, master_fd
    master_fd = fd
    data = os.read(fd, 4096)
    buf += data

    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        stripped = ANSI_RE.sub(b"", line)
        started = STARTED_RE.match(stripped)
        if started:
            not_started.discard(started.group(1))
        m = PREFIX_RE.match(stripped)
        if m:
            log = get_log(m.group(1))
            # Strip the honcho prefix, keep only the service's own output
            content = stripped[stripped.index(b"| ") + 2 :]
            log.write(content + b"\n")
            log.flush()

    if stop_pending:
        send_interrupt()
    return data


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def stop() -> None:
    try:
        pid = int(PIDFILE.read_text())
    except (FileNotFoundError, ValueError):
        print("dev stack is not running")
        return
    if not is_running(pid):
        print("dev stack is not running (stale logs/dev.pid)")
        PIDFILE.unlink(missing_ok=True)
        return

    os.kill(pid, signal.SIGTERM)
    # honcho gives its services 5 seconds before it kills them.
    deadline = time.monotonic() + 30
    while is_running(pid):
        if time.monotonic() > deadline:
            sys.exit(f"dev stack (pid {pid}) did not stop within 30 seconds")
        time.sleep(0.2)
    print("dev stack stopped")


def main() -> None:
    if sys.argv[1:] == ["stop"]:
        stop()
        return

    LOGS.mkdir(exist_ok=True)
    os.chdir(ROOT)
    PIDFILE.write_text(f"{os.getpid()}\n")

    if sys.argv[1:]:
        not_started.update(name.encode() for name in sys.argv[1:])
    else:
        for line in Path("Procfile.dev").read_text().splitlines():
            if m := PROCFILE_RE.match(line):
                not_started.add(m.group(1).encode())

    # Force colors in subprocesses — honcho pipes their output, so they
    # don't see a TTY. These env vars re-enable colors for common tools.
    os.environ["FORCE_COLOR"] = "1"  # Node.js / chalk / Vite
    os.environ["PY_COLORS"] = "1"  # Python tools that check this

    # Forward signals to honcho as a Ctrl-C (see on_signal).
    # When honcho exits, the PID namespace ensures all local children are killed.
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Run honcho inside a PTY (for colors) and a PID namespace (for cleanup).
    status = pty.spawn(
        ["unshare", "--user", "--pid", "--fork",
         "cexec", "modern-app",
         "uv", "run", "honcho", "start", "-f", "Procfile.dev"] + sys.argv[1:],
        read,
    )

    for f in log_files.values():
        f.close()
    PIDFILE.unlink(missing_ok=True)

    exit_code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
