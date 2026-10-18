"""Local suite runner — runs the FieldnotesApp backend + frontend test suites.

The same command runs locally and in CI (the Jenkinsfile's validation Job).

Flow:
  1. install the backend (uv)
  2. wait for services, when backend/scripts/wait-for-services.py exists
  3. run backend pytest
  4. install the frontend's npm deps (standalone pnpm project), build the
     frontend, install the Playwright browser, run Playwright on the dev server
  5. the preview pass: rebuild the frontend with VITE_TEST_MODE=true and run
     the specs tagged @preview on that build (PLAYWRIGHT_PREVIEW_MODE=true)

Step 2 is the app's hook for sidecars that are slow to come up in CI (a search
engine, a message broker): the script runs under the backend's uv venv and
must exit non-zero if a service never becomes ready.

Step 5 covers what only a production build does, such as the SSE SharedWorker:
those specs skip on the dev server. The runner owns VITE_TEST_MODE and
PLAYWRIGHT_PREVIEW_MODE, clearing both for step 4 and setting them for step 5,
and it skips step 5 when the selected specs hold no @preview one. It leaves
dist/ a test-mode build; .dockerignore keeps dist/ out of the image, whose
build stays plain.

The build cache (--build-cache, or BUILD_CACHE=true, which CI sets) restores
the frontend's pnpm store before the install and uploads it after a miss,
keyed on frontend/pnpm-lock.yaml. scripts/build-cache-get.sh and -put.sh are
copies of the Jenkins chart's (HomelabAppsDeploy, jenkins/chart/scripts/).

Output modes:
  simple  — progress indicators, captured output, a summary and test_results.md
            with each suite's passed/failed/skipped counts (default)
  full    — streamed output, and SUITE_RESULT markers from the JUnit XML
            reports in --junitxml-dir (for CI)

Each test run writes a JUnit XML report, in either mode: into --junitxml-dir
when given (backend.xml, frontend.xml, frontend-preview.xml), into a temporary
directory otherwise. The counts come from those reports.
"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

from . import ALL_SUITES, REPO_ROOT, RESULTS_FILE, SCRIPTS_DIR
from .display import (
    is_full_mode,
    progress_end,
    progress_skip,
    progress_start,
    run_with_pager,
    set_full_mode,
)
from .junit import Counts, format_counts, report_counts
from .process import run, run_streamed, run_tracked

APP_NAME = "FieldnotesApp"

# The backend requires Python 3.13 (e.g. queue.ShutDown). Pin its uv venv to
# python3.13 when that interpreter is on PATH; in CI the base image's default
# python is already 3.13 (the binary may be absent by that name), so we skip it.
HAS_PYTHON313 = shutil.which("python3.13") is not None

# The cache key is <prefix>-<sha256 of the lockfile>.tgz, so one prefix serves
# every app: the same lockfile makes the same store.
BUILD_CACHE_PREFIX = "pnpm"

# Specs that need a production build carry this tag; the preview pass runs them.
PREVIEW_TAG = "@preview"


class StepResult(NamedTuple):
    step: str
    ok: bool
    detail: str
    peak_mb: float | None
    # A test run (pytest, a Playwright pass): its counts come from its JUnit
    # report, and are None when it wrote none (a crash, a timeout).
    suite: bool = False
    counts: Counts | None = None


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
  %(prog)s --suite backend --junitxml-dir results   # keep the JUnit XML reports
  %(prog)s --output-mode full --junitxml-dir /work/results --retries 2   # as CI runs it""",
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
                        help="Output mode: simple (progress, a summary with each suite's "
                             "counts, test_results.md) or full (streamed output, and "
                             "SUITE_RESULT markers when --junitxml-dir is given, for CI)")
    parser.add_argument("--junitxml-dir", default=None,
                        help="Directory to keep the JUnit XML reports in (backend.xml, "
                             "frontend.xml, frontend-preview.xml), in either output mode "
                             "(default: a temporary directory)")
    parser.add_argument("--build-cache", action="store_true",
                        default=os.environ.get("BUILD_CACHE") == "true",
                        help="Restore the frontend's pnpm store from the Jenkins build cache and "
                             "upload it after a miss (default: on when BUILD_CACHE=true, as in CI)")
    args = parser.parse_args(argv)

    args.suites = [s.strip() for s in args.suite.split(",") if s.strip()]
    for s in args.suites:
        if s not in ALL_SUITES:
            parser.error(f"Unknown suite: {s!r}. Choose from: {', '.join(ALL_SUITES)}")
    if not args.suites:
        parser.error("--suite requires at least one suite")

    args.full = args.output_mode == "full"

    # Absolute: pytest and Playwright run in backend/ and frontend/.
    if args.junitxml_dir:
        args.junitxml_dir = str(Path(args.junitxml_dir).resolve())
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


def _env_without(*names):
    return {k: v for k, v in os.environ.items() if k not in names}


# ---------------------------------------------------------------------------
# Build cache
# ---------------------------------------------------------------------------

def _pnpm_store_path(frontend):
    """The store the frontend's pnpm installs into, or None if pnpm can't say."""
    try:
        result = subprocess.run(
            ["pnpm", "store", "path"], cwd=frontend, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None
    path = result.stdout.strip()
    return path if result.returncode == 0 and path else None


def _build_cache(action, frontend, store_path):
    """Get or put the pnpm store. A failure only costs time, so it is no step result."""
    script = SCRIPTS_DIR / f"build-cache-{action}.sh"
    ok, _detail = _install_cmd(
        ["sh", str(script), BUILD_CACHE_PREFIX, "pnpm-lock.yaml", store_path],
        cwd=frontend, timeout=300,
    )
    return ok


# ---------------------------------------------------------------------------
# Playwright
# ---------------------------------------------------------------------------

def _run_playwright(args, report_dir, frontend, extra, results, *, preview):
    """Run one Playwright pass and record it. Returns success."""
    cmd = ["pnpm", "playwright", "test", "--reporter=list,junit"]
    if args.max_failures is not None:
        cmd.append(f"--max-failures={args.max_failures}")
    if args.retries is not None:
        cmd.append(f"--retries={args.retries}")
    if args.workers is not None:
        cmd.append(f"--workers={args.workers}")
    env = _env_without("PLAYWRIGHT_PREVIEW_MODE")
    env["PLAYWRIGHT_BACKEND_LOG_STREAM"] = "true"
    junit_name = "frontend"
    label = "frontend playwright"
    if preview:
        cmd.extend(["--grep", PREVIEW_TAG])
        env["PLAYWRIGHT_PREVIEW_MODE"] = "true"
        junit_name = "frontend-preview"
        label = "frontend playwright (preview)"
    report = report_dir / f"{junit_name}.xml"
    report.unlink(missing_ok=True)
    env["PLAYWRIGHT_JUNIT_OUTPUT_NAME"] = str(report)
    cmd.extend(extra)

    col = progress_start(f"Running {label} tests")
    ok, detail, peak_mb = _run_cmd(cmd, cwd=frontend, timeout=3600, env=env)
    progress_end(ok, col)
    results.append(StepResult(label, ok, detail, peak_mb, suite=True,
                              counts=report_counts(report)))
    return ok


def _count_preview_specs(frontend, extra):
    """How many selected tests carry PREVIEW_TAG: (count or None, listing output)."""
    ok, out = run(
        ["pnpm", "playwright", "test", "--list", "--pass-with-no-tests", "--grep", PREVIEW_TAG,
         *extra],
        cwd=frontend, timeout=300,
    )
    match = re.search(r"^Total: (\d+) tests?", out, re.MULTILINE)
    return (int(match.group(1)) if ok and match else None), out


def _run_preview_pass(args, report_dir, frontend, extra, results):
    """Rebuild in test mode and run the @preview specs on that build. Returns success.

    Skipped when the selection holds no @preview spec, so a run of other spec
    files doesn't pay for a second build.
    """
    count, listing = _count_preview_specs(frontend, extra)
    if count is None:
        col = progress_start("Listing frontend preview specs")
        progress_end(False, col)
        if is_full_mode():
            print(listing, flush=True)
        results.append(StepResult("frontend preview listing", False, listing, None))
        return False
    if count == 0:
        col = progress_start(f"Preview pass: no {PREVIEW_TAG} specs selected")
        progress_skip(col)
        return True

    # VITE_TEST_MODE puts the Playwright bridges into the bundle.
    col = progress_start("Building frontend (test mode)")
    ok, detail = _install_cmd(
        ["pnpm", "build"], cwd=frontend, timeout=900,
        env={**os.environ, "VITE_TEST_MODE": "true"},
    )
    progress_end(ok, col)
    results.append(StepResult("frontend build (test mode)", ok, detail, None))
    if not ok:
        col = progress_start("Running frontend playwright (preview) tests")
        progress_skip(col)
        results.append(StepResult("frontend playwright (preview)", False, "Skipped (build failed)", None))
        return False

    return _run_playwright(args, report_dir, frontend, extra, results, preview=True)


# ---------------------------------------------------------------------------
# Suite orchestration
# ---------------------------------------------------------------------------

def run_tests(args, report_dir):
    """Run test steps for selected suites. Returns (exit_code, results).

    results is a list of StepResult; the test runs write their JUnit XML
    reports into report_dir.
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
        cmds = [["uv", "sync", "--locked"] + (["--python", "python3.13"] if HAS_PYTHON313 else [])]
        ok, detail = True, ""
        for cmd in cmds:
            ok, detail = _install_cmd(cmd, cwd=backend, timeout=600)
            if not ok:
                break
        progress_end(ok, col)
        results.append(StepResult("backend install", ok, detail, None))
        backend_installed = ok
    else:
        progress_end(False, col)
        results.append(StepResult("backend install", False, f"Directory not found: {backend}", None))

    # --- Service readiness (app hook) ---
    wait_script = backend / "scripts" / "wait-for-services.py"
    if backend_installed and wait_script.is_file():
        col = progress_start("Waiting for services")
        ok, detail = _install_cmd(
            ["uv", "run", "python", str(wait_script)], cwd=backend, timeout=300
        )
        progress_end(ok, col)
        results.append(StepResult("services", ok, detail, None))
        if not ok:
            exit_code = 1

    # --- Backend tests ---
    if "backend" in args.suites:
        col = progress_start("Running backend tests")
        if not backend_installed:
            progress_skip(col)
            results.append(StepResult("backend pytest", False, "Skipped (install failed)", None))
        else:
            report = report_dir / "backend.xml"
            report.unlink(missing_ok=True)
            pytest_cmd = ["uv", "run", "pytest", "-v", "--tb=short", f"--junitxml={report}"]
            if args.max_failures is not None:
                pytest_cmd.append(f"--maxfail={args.max_failures}")
            pytest_cmd.extend(extra_args["backend"])
            ok, detail, peak_mb = _run_cmd(pytest_cmd, cwd=backend, timeout=1800)
            progress_end(ok, col)
            results.append(StepResult("backend pytest", ok, detail, peak_mb, suite=True,
                                      counts=report_counts(report)))
            if not ok:
                exit_code = 1

    # --- Frontend ---
    if "frontend" in args.suites:
        cache_store = None
        cache_hit = False
        if args.build_cache and frontend.is_dir():
            col = progress_start("Restoring pnpm store from build cache")
            cache_store = _pnpm_store_path(frontend)
            cache_hit = bool(cache_store) and _build_cache("get", frontend, cache_store)
            if cache_hit:
                progress_end(True, col)
            else:
                progress_skip(col)

        # The frontend is a standalone pnpm project (its own lockfile), not a
        # workspace member — install it in its own directory.
        col = progress_start("Installing npm dependencies")
        pnpm_install = ["pnpm", "install", "--frozen-lockfile", "--config.confirmModulesPurge=false"]
        ok, detail = _install_cmd(pnpm_install, cwd=frontend, timeout=600)
        frozen_ok = ok
        if not ok:
            ok, detail = _install_cmd(
                ["pnpm", "install", "--config.confirmModulesPurge=false"],
                cwd=frontend, timeout=600,
            )
        progress_end(ok, col)
        results.append(StepResult("frontend install", ok, detail, None))
        pnpm_installed = ok

        # Only a frozen install's store is the one the lockfile's hash names.
        if cache_store and frozen_ok and not cache_hit:
            col = progress_start("Uploading pnpm store to build cache")
            progress_end(_build_cache("put", frontend, cache_store), col)

        if not frontend.is_dir():
            col = progress_start("Building frontend")
            progress_end(False, col)
            results.append(StepResult("frontend build", False, f"Directory not found: {frontend}", None))
        elif not pnpm_installed:
            for step in ("build", "playwright"):
                col = progress_start(f"Running frontend {step}" if step != "build" else "Building frontend")
                progress_skip(col)
                results.append(StepResult(f"frontend {step}", False, "Skipped (install failed)", None))
        else:
            # Build (generates the API client + routes, runs check, vite build),
            # plain like the image's.
            col = progress_start("Building frontend")
            ok, detail = _install_cmd(
                ["pnpm", "build"], cwd=frontend, timeout=900, env=_env_without("VITE_TEST_MODE"),
            )
            progress_end(ok, col)
            results.append(StepResult("frontend build", ok, detail, None))

            if not ok:
                exit_code = 1
                col = progress_start("Running frontend playwright tests")
                progress_skip(col)
                results.append(StepResult("frontend playwright", False, "Skipped (build failed)", None))
            else:
                # Download the Chromium build the installed Playwright pins; a no-op
                # when it is already in the browser cache. The CI validation image
                # carries no browser, so every CI run downloads it.
                col = progress_start("Installing Playwright browser")
                ok_pw, detail_pw = _install_cmd(
                    ["pnpm", "playwright", "install", "chromium"], cwd=frontend, timeout=900
                )
                progress_end(ok_pw, col)
                results.append(StepResult("playwright install", ok_pw, detail_pw, None))

                if not _run_playwright(args, report_dir, frontend, extra_args["frontend"],
                                       results, preview=False):
                    exit_code = 1
                if not _run_preview_pass(args, report_dir, frontend, extra_args["frontend"],
                                         results):
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
        counts = report_counts(xml_file) or Counts(0, 0, 0)
        print(f"===SUITE_RESULT:{xml_file.stem}:{counts.passed}:{counts.failed}:"
              f"{counts.skipped}===", flush=True)


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


def _format_suite_counts(steps):
    """A line per test run with its passed/failed/skipped counts, under a header."""
    return [f"  {s.step}: {format_counts(s.counts)}" for s in steps if s.suite]


def format_summary(all_results):
    lines = []
    any_failure = False

    for app_name, steps in all_results:
        failures = [s for s in steps if not s.ok]
        if failures:
            any_failure = True
            lines.append(f"\n{app_name}: FAILURES")
            for s in failures:
                mem = _format_mem(s.peak_mb)
                if "Skipped" in s.detail:
                    lines.append(f"  {s.step}: {s.detail}")
                elif s.step == "backend pytest":
                    extracted = _extract_pytest_failures(s.detail)
                    for line in extracted:
                        lines.append(f"  {line}")
                    if not extracted:
                        lines.append(f"  {s.step}: failed{mem} (see test_results.md)")
                    elif mem:
                        lines.append(f"  {mem.strip()}")
                elif "playwright" in s.step:
                    extracted = _extract_playwright_failures(s.detail)
                    for line in extracted:
                        lines.append(f"  {line}")
                    if not extracted:
                        lines.append(f"  {s.step}: failed{mem} (see test_results.md)")
                    elif mem:
                        lines.append(f"  {mem.strip()}")
                else:
                    lines.append(f"  {s.step}: FAILED (see test_results.md)")
        else:
            mem_parts = [f"{s.step}{_format_mem(s.peak_mb)}" for s in steps]
            lines.append(f"\n{app_name}: all passed ({', '.join(mem_parts)})")

        suite_counts = _format_suite_counts(steps)
        if suite_counts:
            lines.append(f"\n{app_name}: test counts")
            lines.extend(suite_counts)

    if not any_failure:
        lines.append("\nAll steps passed.")

    return "\n".join(lines) + "\n"


def format_app_detailed(app_name, steps):
    lines = [f"## {app_name}\n"]
    for s in steps:
        status = "PASS" if s.ok else "FAIL"
        counts = f" ({format_counts(s.counts)})" if s.suite else ""
        lines.append(f"### {s.step}: {status}{counts}{_format_mem(s.peak_mb)}\n")
        if not s.ok and s.detail and "Skipped" not in s.detail:
            lines.append("```")
            lines.extend(s.detail.splitlines())
            lines.append("```\n")
        elif not s.ok:
            lines.append(f"{s.detail}\n")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def main(argv=None):
    args = parse_args(argv)
    set_full_mode(args.full)

    # The test runs always write JUnit XML reports, for the counts; without
    # --junitxml-dir they go to a directory that lives as long as the run.
    with tempfile.TemporaryDirectory(prefix="run-suite-") as tmp:
        exit_code, steps = run_tests(args, Path(args.junitxml_dir or tmp))

    if is_full_mode():
        # Full mode: emit SUITE_RESULT markers from the kept JUnit XML
        if args.junitxml_dir:
            col = progress_start("Exporting test results")
            _emit_suite_results(args.junitxml_dir)
            progress_end(True, col)
    else:
        # Simple mode: write test_results.md and print the summary, with counts
        RESULTS_FILE.write_text("# Test Results\n\n")
        with RESULTS_FILE.open("a") as f:
            f.write(format_app_detailed(APP_NAME, steps))
        print(f"\nDetailed results written to {RESULTS_FILE}", file=sys.stderr)
        print(format_summary([(APP_NAME, steps)]))

    sys.exit(exit_code)


def cli():
    run_with_pager(main)
