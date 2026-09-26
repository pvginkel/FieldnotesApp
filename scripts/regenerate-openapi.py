#!/usr/bin/env python3
"""Regenerate the OpenAPI cache for one or more frontend subprojects.

Picks a free port, starts the backend, waits for the OpenAPI spec
endpoint to be reachable, runs `pnpm generate:api` in the requested
subproject(s), and stops the backend cleanly on exit.

We poll `/api/docs/openapi.json` directly rather than `/health/readyz`:
the readiness probe can sit at HTTP 503 indefinitely when a non-critical
dependency is degraded in the local dev environment, even though the
OpenAPI endpoint itself is perfectly usable. What we actually care
about is whether the downstream `generate:api` fetch will succeed, so
that's what we poll.

## Customize for your project

The defaults below assume:
- A `backend/` subproject started with `poetry run dev` (long-running server).
  The backend has no database, so there is no one-shot prepare step.
- The OpenAPI spec lives at `/api/docs/openapi.json`.
- A `frontend/` subproject with a `pnpm generate:api` script in its
  `package.json`.

If your stack differs, edit the constants and the `start_backend` helper.
The flow (start backend → wait → generate → stop) is the load-bearing part.

Usage:
    scripts/regenerate-openapi.py --frontend
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
LOG_DIR = ROOT / "logs"

OPENAPI_PATH = "/api/docs/openapi.json"
READY_TIMEOUT_S = 120


def pick_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def wait_for_ready(port: int, dev_proc: subprocess.Popen[bytes], timeout_s: int) -> None:
    """Poll the OpenAPI spec endpoint until it returns 200 or the deadline expires.

    Fails fast if the backend process has exited.
    """
    url = f"http://localhost:{port}{OPENAPI_PATH}"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rc = dev_proc.poll()
        if rc is not None:
            raise RuntimeError(
                f"Backend exited before becoming ready (exit code {rc}). "
                f"See {LOG_DIR / 'regenerate-openapi-backend.log'}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 300:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    raise RuntimeError(
        f"Backend did not become ready on port {port} within {timeout_s}s "
        f"(polled {url}). See {LOG_DIR / 'regenerate-openapi-backend.log'}"
    )


def stop_backend(dev_proc: subprocess.Popen[bytes]) -> None:
    if dev_proc.poll() is not None:
        return
    try:
        os.killpg(dev_proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        dev_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(dev_proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        dev_proc.wait(timeout=5)


def start_backend(port: int, log_path: Path) -> subprocess.Popen[bytes]:
    env = {**os.environ, "PORT": str(port)}
    log_file = open(log_path, "wb")
    try:
        return subprocess.Popen(
            ["poetry", "run", "dev"],
            cwd=BACKEND_DIR,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        log_file.close()
        raise


def regenerate(project: str, port: int) -> int:
    project_dir = ROOT / project
    env = {**os.environ, "PORT": str(port)}
    print(f"[{project}] pnpm generate:api", flush=True)
    result = subprocess.run(
        ["pnpm", "generate:api"],
        cwd=project_dir,
        env=env,
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", action="store_true", help="Regenerate frontend OpenAPI client")
    parser.add_argument(
        "--timeout",
        type=int,
        default=READY_TIMEOUT_S,
        help="Seconds to wait for backend readiness (default: %(default)s)",
    )
    args = parser.parse_args()

    targets: list[str] = []
    if args.frontend:
        targets.append("frontend")
    if not targets:
        parser.error("Specify --frontend")

    LOG_DIR.mkdir(exist_ok=True)
    backend_log = LOG_DIR / "regenerate-openapi-backend.log"

    port = pick_free_port()
    print(f"Starting backend on port {port} (log: {backend_log})", flush=True)
    dev_proc = start_backend(port, backend_log)

    try:
        wait_for_ready(port, dev_proc, args.timeout)
        print(f"Backend ready on port {port}", flush=True)
        for target in targets:
            rc = regenerate(target, port)
            if rc != 0:
                print(f"generate:api failed for {target}", file=sys.stderr)
                return rc
    finally:
        stop_backend(dev_proc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
