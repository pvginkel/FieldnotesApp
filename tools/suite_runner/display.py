"""Terminal display helpers — colors, progress indicators, pager.

Output modes:

  simple  — progress indicators with right-aligned status tags (default)
  full    — ``=== Step ===`` headers, no status tags (for CI / streamed logs)

Call ``set_full_mode(True)`` once at startup to switch.  All progress_*
functions adapt automatically — callers never need to branch on the mode.
"""

import io
import os
import subprocess
import sys
import time

COLS = 80
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"

USE_COLOR = sys.stderr.isatty()

_full_mode = False


def set_full_mode(enabled: bool) -> None:
    """Switch between simple (progress bars) and full (CI headers) output."""
    global _full_mode
    _full_mode = enabled


def is_full_mode() -> bool:
    return _full_mode


def colorize(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if USE_COLOR else text


_progress_start_time = 0.0


def _elapsed_suffix() -> str:
    elapsed = time.monotonic() - _progress_start_time
    if elapsed < 60:
        return f"({elapsed:.0f}s)"
    return f"({elapsed / 60:.1f}m)"


def progress_start(msg: str) -> int:
    """Begin a progress step.

    Simple mode: prints ``' * msg...'`` without newline and returns column width.
    Full mode: prints ``'=== msg ==='`` with newline and returns 0.
    """
    global _progress_start_time
    _progress_start_time = time.monotonic()
    if _full_mode:
        print(f"=== {msg} ===", flush=True)
        return 0
    line = f" * {msg}..."
    print(line, end="", file=sys.stderr, flush=True)
    return len(line)


def progress_end(ok: bool, col_used: int) -> None:
    """Finish a progress step with [ OK ] or [FAIL].  No-op in full mode."""
    if _full_mode:
        return
    suffix = _elapsed_suffix()
    tag = colorize(GREEN, "[ OK ]") if ok else colorize(RED, "[FAIL]")
    tag_plain = "[ OK ]" if ok else "[FAIL]"
    padding = max(1, COLS - col_used - len(f" {suffix} ") - len(tag_plain))
    print(f" {suffix}" + " " * padding + tag, file=sys.stderr, flush=True)


def progress_skip(col_used: int) -> None:
    """Mark a progress step as skipped.  No-op in full mode."""
    if _full_mode:
        return
    tag = colorize(YELLOW, "[skip]")
    padding = max(1, COLS - col_used - len("[skip]"))
    print(" " * padding + tag, file=sys.stderr, flush=True)


def progress_header(title: str) -> None:
    """Print a boot-style section header."""
    print(file=sys.stderr)
    print(colorize(BOLD, f" --- {title} ---"), file=sys.stderr, flush=True)


def run_with_pager(fn):
    """Run fn(), capturing stdout and piping it through less if on a TTY."""
    if not sys.stdout.isatty():
        fn()
        return

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    exit_code = 0
    try:
        fn()
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    if output:
        pager = subprocess.Popen(
            ["less", "-R"],
            stdin=subprocess.PIPE,
            env={**os.environ, "LESSCHARSET": "utf-8"},
        )
        try:
            pager.communicate(input=output.encode())
        except KeyboardInterrupt:
            pager.kill()

    if exit_code:
        raise SystemExit(exit_code)
