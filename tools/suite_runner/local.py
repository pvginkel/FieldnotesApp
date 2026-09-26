"""Local suite runner — runs the FieldnotesApp backend + frontend test suites.

The same command runs locally and in CI (the Jenkinsfile's validation Job).

Flow:
  1. install the backend (Poetry)
  2. wait for services, when backend/scripts/wait-for-services.py exists
  3. run backend pytest
  4. install the frontend's npm deps (standalone pnpm project), build the
     frontend, install the Playwright browser, run Playwright

Step 2 is the app's hook for sidecars that are slow to come up in CI (a search
engine, a message broker): the script runs under the backend's Poetry venv and
must exit non-zero if a service never becomes ready.

Output modes:
  simple  — progress indicators, captured output, test_results.md (default)
  full    — streamed output, JUnit XML, SUITE_RESULT markers (for CI)
"""

import argparse
import os
import shlex
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from . import ALL_SUITES, REPO_ROOT, RESULTS_FILE
from .display import (
    is_full_mode,
    progress_end,
    progress_skip,
    progress_start,
    run_with_pager,
    set_full_mode,
)
from .process import run, run_streamed, run_tracked

APP_NAME = "FieldnotesApp"

# The backend requires Python 3.13 (e.g. queue.ShutDown). Pin its Poetry venv to
# python3.13 when that interpreter is on PATH; in CI the base image's default
# python is already 3.13 (the binary may be absent by that name), so we skip it.
HAS_PYTHON313 = shutil.which("python3.13") is not None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=f"Run the {APP_NAME} backend and frontend test suites.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s --suite backend
  %(prog)s --suite backend --backend-args "tests/test_foo.py -k bar"
  %(prog)s --suite frontend --frontend-args "tests/e2e/foo.spec.ts"
  %(prog)s --max-failures 10
  %(prog)s --output-mode full --junitxml-dir /work/results --retries 2""",
    )
    parser.add_argument(
        "--suite", default=",".join(ALL_SUITES),
        help="Comma-separated suites: backend, frontend (default: all)",
    )
    parser.add_argument("--backend-args", default="",
                        help='Extra pytest arguments (e.g., "tests/test_foo.py -k bar")')
    parser.add_argument("--frontend-args", default="",
                        help='Extra playwright arguments')
    parser.add_argument("--max-failures", type=int, default=None,
                        help="Max failures before stopping (pytest --maxfail / playwright --max-failures)")
    parser.add_argument("--retries", type=int, default=None,
                        help="Playwright retry count for failed tests (default: Playwright config)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Playwright parallel worker count (default: Playwright config)")
    parser.add_argument("--output-mode", choices=["simple", "full"], default="simple",
                        help="Output mode: simple (progress + summary) or full (streamed + JUnit)")
    parser.add_argument("--junitxml-dir", default=None,
                        help="Directory for JUnit XML output (used with --output-mode full)")
    args = parser.parse_args(argv)

    args.suites = [s.strip() for s in args.suite.split(",") if s.strip()]
    for s in args.suites:
        if s not in ALL_SUITES:
            parser.error(f"Unknown suite: {s!r}. Choose from: {', '.join(ALL_SUITES)}")
    if not args.suites:
        parser.error("--suite requires at least one suite")

    args.full = args.output_mode == "full"

    if args.junitxml_dir:
        Path(args.junitxml_dir).mkdir(parents=True, exist_ok=True)

    return args


# ---------------------------------------------------------------------------
# Command helpers
# ---------------------------------------------------------------------------

def _run_cmd(cmd, *, cwd, timeout=120, env=None):
    """Run a test command, choosing streamed or tracked based on output mode.

    Returns ``(ok, detail, peak_mb)`` — in full mode *detail* is always
    empty and *peak_mb* is always None.
    """
    if is_full_mode():
        ok = run_streamed(cmd, cwd=cwd, timeout=timeout, env=env)
        return ok, "", None
    ok, detail, peak_mb = run_tracked(cmd, cwd=cwd, timeout=timeout, env=env)
    return ok, detail, peak_mb


def _install_cmd(cmd, *, cwd, timeout=120, env=None):
    """Run an install/build command (no memory tracking).

    Returns ``(ok, detail)`` — in full mode *detail* is always empty.
    """
    if is_full_mode():
        ok = run_streamed(cmd, cwd=cwd, timeout=timeout, env=env)
        return ok, ""
    return run(cmd, cwd=cwd, timeout=timeout, env=env)


# ---------------------------------------------------------------------------
# Suite orchestration
# ---------------------------------------------------------------------------

def run_tests(args):
    """Run test steps for selected suites. Returns (exit_code, results).

    results is a list of (step, ok, detail, peak_mb) tuples.
    """
    backend = REPO_ROOT / "backend"
    frontend = REPO_ROOT / "frontend"
    results = []
    exit_code = 0
    extra_args = {
        "backend": shlex.split(args.backend_args) if args.backend_args else [],
        "frontend": shlex.split(args.frontend_args) if args.frontend_args else [],
    }

    # --- Backend install (always — the frontend's Playwright harness boots the
    #     backend per worker, so the backend venv is needed even for frontend-only runs) ---
    backend_installed = False
    col = progress_start("Installing backend dependencies")
    if backend.is_dir():
        cmds = []
        if HAS_PYTHON313:
            cmds.append(["poetry", "env", "use", "python3.13"])
        cmds.append(["poetry", "install", "--no-interaction"])
        ok, detail = True, ""
        for cmd in cmds:
            ok, detail = _install_cmd(cmd, cwd=backend, timeout=600)
            if not ok:
                break
        progress_end(ok, col)
        results.append(("backend install", ok, detail, None))
        backend_installed = ok
    else:
        progress_end(False, col)
        results.append(("backend install", False, f"Directory not found: {backend}", None))

    # --- Service readiness (app hook) ---
    wait_script = backend / "scripts" / "wait-for-services.py"
    if backend_installed and wait_script.is_file():
        col = progress_start("Waiting for services")
        ok, detail = _install_cmd(
            ["poetry", "run", "python", str(wait_script)], cwd=backend, timeout=300
        )
        progress_end(ok, col)
        results.append(("services", ok, detail, None))
        if not ok:
            exit_code = 1

    # --- Backend tests ---
    if "backend" in args.suites:
        col = progress_start("Running backend tests")
        if not backend_installed:
            progress_skip(col)
            results.append(("backend pytest", False, "Skipped (install failed)", None))
        else:
            pytest_cmd = ["poetry", "run", "pytest", "-v", "--tb=short"]
            if args.junitxml_dir:
                pytest_cmd.append(f"--junitxml={args.junitxml_dir}/backend.xml")
            if args.max_failures is not None:
                pytest_cmd.append(f"--maxfail={args.max_failures}")
            pytest_cmd.extend(extra_args["backend"])
            ok, detail, peak_mb = _run_cmd(pytest_cmd, cwd=backend, timeout=1800)
            progress_end(ok, col)
            results.append(("backend pytest", ok, detail, peak_mb))
            if not ok:
                exit_code = 1

    # --- Frontend ---
    if "frontend" in args.suites:
        # The frontend is a standalone pnpm project (its own lockfile), not a
        # workspace member — install it in its own directory.
        col = progress_start("Installing npm dependencies")
        pnpm_install = ["pnpm", "install", "--frozen-lockfile", "--config.confirmModulesPurge=false"]
        ok, detail = _install_cmd(pnpm_install, cwd=frontend, timeout=600)
        if not ok:
            ok, detail = _install_cmd(
                ["pnpm", "install", "--config.confirmModulesPurge=false"],
                cwd=frontend, timeout=600,
            )
        progress_end(ok, col)
        results.append(("frontend install", ok, detail, None))
        pnpm_installed = ok

        if not frontend.is_dir():
            col = progress_start("Building frontend")
            progress_end(False, col)
            results.append(("frontend build", False, f"Directory not found: {frontend}", None))
        elif not pnpm_installed:
            for step in ("build", "playwright"):
                col = progress_start(f"Running frontend {step}" if step != "build" else "Building frontend")
                progress_skip(col)
                results.append((f"frontend {step}", False, "Skipped (install failed)", None))
        else:
            # Build (generates the API client + routes, runs check, vite build).
            col = progress_start("Building frontend")
            ok, detail = _install_cmd(["pnpm", "build"], cwd=frontend, timeout=900)
            progress_end(ok, col)
            results.append(("frontend build", ok, detail, None))

            if not ok:
                exit_code = 1
                col = progress_start("Running frontend playwright tests")
                progress_skip(col)
                results.append(("frontend playwright", False, "Skipped (build failed)", None))
            else:
                # Download the Chromium build the installed Playwright pins; a no-op
                # when it is already in the browser cache. The CI validation image
                # carries no browser, so every CI run downloads it.
                col = progress_start("Installing Playwright browser")
                ok_pw, detail_pw = _install_cmd(
                    ["pnpm", "playwright", "install", "chromium"], cwd=frontend, timeout=900
                )
                progress_end(ok_pw, col)
                results.append(("playwright install", ok_pw, detail_pw, None))

                reporter = "list,junit" if args.junitxml_dir else "list"
                pw_cmd = ["pnpm", "playwright", "test", f"--reporter={reporter}"]
                if args.max_failures is not None:
                    pw_cmd.append(f"--max-failures={args.max_failures}")
                if args.retries is not None:
                    pw_cmd.append(f"--retries={args.retries}")
                if args.workers is not None:
                    pw_cmd.append(f"--workers={args.workers}")
                pw_env = {**os.environ, "PLAYWRIGHT_BACKEND_LOG_STREAM": "true"}
                if args.junitxml_dir:
                    pw_env["PLAYWRIGHT_JUNIT_OUTPUT_NAME"] = f"{args.junitxml_dir}/frontend.xml"
                pw_cmd.extend(extra_args["frontend"])

                col = progress_start("Running frontend playwright tests")
                ok, detail, peak_mb = _run_cmd(pw_cmd, cwd=frontend, timeout=3600, env=pw_env)
                progress_end(ok, col)
                results.append(("frontend playwright", ok, detail, peak_mb))
                if not ok:
                    exit_code = 1

    return exit_code, results


# ---------------------------------------------------------------------------
# JUnit XML → SUITE_RESULT markers
# ---------------------------------------------------------------------------

def _emit_suite_results(junitxml_dir):
    """Parse JUnit XML files and emit SUITE_RESULT markers for CI."""
    xml_dir = Path(junitxml_dir)
    if not xml_dir.is_dir():
        return

    for xml_file in sorted(xml_dir.glob("*.xml")):
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
            # For a <testsuites> root, aggregate across all <testsuite> children
            # (Playwright emits one per spec file). Sum children to stay robust
            # against reporters that only populate per-suite counts.
            if root.tag == "testsuites":
                suites = root.findall("testsuite")
            else:
                suites = [root]
            tests = sum(int(s.get("tests", 0)) for s in suites)
            failures = sum(int(s.get("failures", 0)) for s in suites)
            errors = sum(int(s.get("errors", 0)) for s in suites)
            skipped = sum(int(s.get("skipped", 0)) for s in suites)
            passed = tests - failures - errors - skipped
            name = xml_file.stem
            print(f"===SUITE_RESULT:{name}:{passed}:{failures + errors}:{skipped}===",
                  flush=True)
        except (ET.ParseError, ValueError):
            print(f"===SUITE_RESULT:{xml_file.stem}:0:0:0===", flush=True)


# ---------------------------------------------------------------------------
# Output formatting (simple mode)
# ---------------------------------------------------------------------------

def _extract_pytest_failures(detail):
    return [line.strip() for line in detail.splitlines() if line.strip().startswith("FAILED ")]


def _extract_playwright_failures(detail):
    failed = []
    for line in detail.splitlines():
        stripped = line.strip()
        if "test-results/" in stripped:
            continue
        if "›" in stripped and ("✘" in stripped or "[chromium]" in stripped):
            failed.append(stripped)
        elif stripped.endswith(" failed") and stripped.split()[0].isdigit():
            failed.append(stripped)
    return failed


def _format_mem(peak_mb):
    if peak_mb is not None and peak_mb > 0:
        return f" [{peak_mb:,.0f} MB peak]"
    return ""


def format_summary(all_results):
    lines = []
    any_failure = False

    for app_name, steps in all_results:
        failures = [(step, detail, peak_mb)
                    for step, ok, detail, peak_mb in steps if not ok]
        if failures:
            any_failure = True
            lines.append(f"\n{app_name}: FAILURES")
            for step, detail, peak_mb in failures:
                mem = _format_mem(peak_mb)
                if "Skipped" in detail:
                    lines.append(f"  {step}: {detail}")
                elif step == "backend pytest":
                    extracted = _extract_pytest_failures(detail)
                    for line in extracted:
                        lines.append(f"  {line}")
                    if not extracted:
                        lines.append(f"  {step}: failed{mem} (see test_results.md)")
                    elif mem:
                        lines.append(f"  {mem.strip()}")
                elif "playwright" in step:
                    extracted = _extract_playwright_failures(detail)
                    for line in extracted:
                        lines.append(f"  {line}")
                    if not extracted:
                        lines.append(f"  {step}: failed{mem} (see test_results.md)")
                    elif mem:
                        lines.append(f"  {mem.strip()}")
                else:
                    lines.append(f"  {step}: FAILED (see test_results.md)")
        else:
            mem_parts = []
            for step, _ok, _detail, peak_mb in steps:
                mem = _format_mem(peak_mb)
                mem_parts.append(f"{step}{mem}")
            lines.append(f"\n{app_name}: all passed ({', '.join(mem_parts)})")

    if not any_failure:
        lines.append("\nAll steps passed.")

    return "\n".join(lines) + "\n"


def format_app_detailed(app_name, steps):
    lines = [f"## {app_name}\n"]
    for step, ok, detail, peak_mb in steps:
        status = "PASS" if ok else "FAIL"
        mem = _format_mem(peak_mb)
        lines.append(f"### {step}: {status}{mem}\n")
        if not ok and detail and "Skipped" not in detail:
            lines.append("```")
            lines.extend(detail.splitlines())
            lines.append("```\n")
        elif not ok:
            lines.append(f"{detail}\n")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(argv)
    set_full_mode(args.full)

    exit_code, steps = run_tests(args)

    if is_full_mode():
        # Full mode: emit SUITE_RESULT markers from JUnit XML
        if args.junitxml_dir:
            col = progress_start("Exporting test results")
            _emit_suite_results(args.junitxml_dir)
            progress_end(True, col)
    else:
        # Simple mode: write test_results.md and print summary
        RESULTS_FILE.write_text("# Test Results\n\n")
        with RESULTS_FILE.open("a") as f:
            f.write(format_app_detailed(APP_NAME, steps))
        print(f"\nDetailed results written to {RESULTS_FILE}", file=sys.stderr)
        print(format_summary([(APP_NAME, steps)]))

    sys.exit(exit_code)


def cli():
    run_with_pager(main)
