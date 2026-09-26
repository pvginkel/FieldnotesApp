"""Subprocess execution with optional memory tracking."""

import subprocess
import sys
import threading

import psutil


def _command_label(args, max_len=80):
    """Render a short, human-readable label for a command argv list.

    Used in the timeout-kill log line. Truncates to *max_len* characters
    so the line stays grep-friendly in the streamed log.
    """
    text = " ".join(str(a) for a in args)
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def _emit_timeout_kill(args, timeout):
    """Emit a single, loud, grep-friendly line marking a timeout kill.

    Written to stdout with flush=True to match the streamed-output
    pattern used by the suite runner; lands in whatever stream CI
    captures from the runner. Called only from the timeout-kill path —
    never on a clean exit.
    """
    print(
        f"=== TIMEOUT: {_command_label(args)} killed after {timeout}s ===",
        flush=True,
        file=sys.stdout,
    )


def run(args, cwd, timeout=600, env=None):
    """Run a command, return (success, stdout+stderr)."""
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {' '.join(str(a) for a in args)}"


def run_streamed(args, cwd, timeout=600, env=None):
    """Run a command with output streaming to stdout/stderr. Returns success."""
    try:
        result = subprocess.run(args, cwd=cwd, timeout=timeout, env=env)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        _emit_timeout_kill(args, timeout)
        return False


def _get_tree_rss_bytes(pid):
    """Get total RSS in bytes for a process and all its descendants."""
    try:
        parent = psutil.Process(pid)
        total = parent.memory_info().rss
        for child in parent.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return total
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0


def run_tracked(args, cwd, timeout=600, env=None):
    """Run a command, return (success, stdout+stderr, peak_memory_mb).

    Like run(), but also tracks peak RSS of the process tree via psutil.
    """
    peak_bytes = 0
    stop_event = threading.Event()

    proc = subprocess.Popen(
        args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=env,
    )

    def _monitor():
        nonlocal peak_bytes
        while not stop_event.is_set():
            rss = _get_tree_rss_bytes(proc.pid)
            if rss > peak_bytes:
                peak_bytes = rss
            stop_event.wait(0.5)

    monitor = threading.Thread(target=_monitor, daemon=True)
    monitor.start()

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stop_event.set()
        monitor.join(timeout=2)
        _emit_timeout_kill(args, timeout)
        return False, f"Command timed out after {timeout}s: {' '.join(str(a) for a in args)}", 0

    stop_event.set()
    monitor.join(timeout=2)

    return proc.returncode == 0, stdout + stderr, peak_bytes / (1024 * 1024)
