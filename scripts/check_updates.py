#!/usr/bin/env python3
"""Report the dependencies that have a newer release, in every component.

It finds the components itself, so a component added later needs no edit here:

- a Python component is a directory with a `uv.lock`, the root and `backend/`
  among them. `uv tree --outdated --depth 1 --frozen` lists its direct
  dependencies at their locked versions, every dependency group included; a uv
  workspace's members are reported through its root's lock;
- an npm component is a directory with a `package.json`. `pnpm outdated`
  lists its direct dependencies at their installed versions, so it needs
  `node_modules` (`kc project setup`).

Each update is classified as major, minor or patch from the version in use and
the latest release, whatever range the manifest allows. A component that cannot
be checked is a warning, and the rest are still reported.

Run it where uv and pnpm are, from anywhere in the repository:

    cexec modern-app python3 scripts/check_updates.py           # report
    cexec modern-app python3 scripts/check_updates.py --json    # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Not searched for components: generated or installed trees, and hidden
# directories (.git, .venv, .kubecoder and the like).
SKIP_DIRS = {"node_modules", "test-app", "dist", "build", "logs", "test-results"}

TIMEOUT_S = 300

# A direct dependency in `uv tree --depth 1`, such as
# `├── mypy v1.20.2 (group: dev) (latest: v2.4.0)`.
_UV_TREE_DEPENDENCY = re.compile(
    r"^[├└]── (?P<name>[^\s\[]+)(?:\[[^\]]*\])? v(?P<version>\S+)(?P<notes>.*)$"
)
# A tree's root, the project or a workspace member: `test-app v0.1.0`.
_UV_TREE_ROOT = re.compile(r"^(?P<name>[^\s├└│(]\S*) v\S+")
_UV_TREE_GROUP = re.compile(r"\((?:group|extra): (?P<group>[^)]+)\)")
_UV_TREE_LATEST = re.compile(r"\(latest: v(?P<latest>[^)]+)\)")


@dataclass
class Update:
    package: str
    current: str
    latest: str
    update_type: str  # major, minor or patch
    location: str  # the component, e.g. "backend (python)"
    dep_group: str  # "dependencies", a dependency group or "devDependencies"


def classify(current: str, latest: str) -> str:
    """Classify an update as major, minor or patch."""

    def parts(version: str) -> list[int]:
        version = re.sub(r"^v", "", version)
        version = re.sub(r"[-+].*", "", version)
        numbers = []
        for part in version.split(".")[:3]:
            match = re.match(r"\d+", part)
            numbers.append(int(match.group()) if match else 0)
        return numbers + [0] * (3 - len(numbers))

    old, new = parts(current), parts(latest)
    if new[0] != old[0]:
        return "major"
    if new[1] != old[1]:
        return "minor"
    return "patch"


def find_components() -> tuple[list[Path], list[Path]]:
    """The Python and npm components, as directories under ROOT."""
    python: list[Path] = []
    npm: list[Path] = []
    for directory, subdirs, files in os.walk(ROOT):
        subdirs[:] = sorted(
            d for d in subdirs if not d.startswith(".") and d not in SKIP_DIRS
        )
        path = Path(directory)
        if "uv.lock" in files and "pyproject.toml" in files:
            python.append(path)
        if "package.json" in files:
            npm.append(path)
    return python, npm


def label(path: Path, kind: str, member: str = "") -> str:
    name = path.relative_to(ROOT).as_posix()
    return (
        f"{'root' if name == '.' else name} ({kind}{', ' + member if member else ''})"
    )


def warn(message: str) -> None:
    print(f"  Warning: {message}", file=sys.stderr)


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    # VIRTUAL_ENV would point uv at the venv of whatever started this script.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        warn(f"{' '.join(command)} in {cwd}: {exc}")
        return None


def python_updates(path: Path) -> list[Update]:
    result = run(["uv", "tree", "--outdated", "--depth", "1", "--frozen"], path)
    if result is None:
        return []
    if result.returncode != 0:
        warn(
            f"could not check {label(path, 'python')}: {result.stderr.strip()[-2000:]}"
        )
        return []

    lines = result.stdout.splitlines()
    roots = [m.group("name") for line in lines if (m := _UV_TREE_ROOT.match(line))]
    updates: list[Update] = []
    root = ""
    for line in lines:
        if m := _UV_TREE_ROOT.match(line):
            root = m.group("name")
            continue
        dependency = _UV_TREE_DEPENDENCY.match(line)
        if not dependency:
            continue
        latest = _UV_TREE_LATEST.search(dependency.group("notes"))
        if not latest:
            continue
        group = _UV_TREE_GROUP.search(dependency.group("notes"))
        current = dependency.group("version")
        updates.append(
            Update(
                package=dependency.group("name"),
                current=current,
                latest=latest.group("latest"),
                update_type=classify(current, latest.group("latest")),
                # A workspace has a root per member.
                location=label(path, "python", root if len(roots) > 1 else ""),
                dep_group=group.group("group") if group else "dependencies",
            )
        )
    return updates


def npm_updates(path: Path) -> list[Update]:
    location = label(path, "npm")
    if not (path / "node_modules").is_dir():
        warn(f"could not check {location}: no node_modules (run kc project setup)")
        return []
    result = run(["pnpm", "outdated", "--format", "json"], path)
    if result is None:
        return []
    # pnpm exits 1 when something is outdated; the report is on stdout either way.
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        warn(f"could not check {location}: {result.stderr.strip()[-2000:]}")
        return []

    updates: list[Update] = []
    for package, info in data.items():
        current = info.get("current", "")
        latest = info.get("latest", "")
        if not current or not latest or current == latest:
            continue
        dev = "dev" in info.get("dependencyType", "").lower()
        updates.append(
            Update(
                package=package,
                current=current,
                latest=latest,
                update_type=classify(current, latest),
                location=location,
                dep_group="devDependencies" if dev else "dependencies",
            )
        )
    return updates


def print_table(updates: list[Update]) -> None:
    if not updates:
        print("  (none)")
        return
    updates.sort(key=lambda u: (u.location, u.package))
    widths = [
        max(len(getattr(u, field)) for u in updates)
        for field in ("package", "current", "latest", "location")
    ]
    for u in updates:
        print(
            f"  {u.package:<{widths[0]}}  {u.current:<{widths[1]}} → "
            f"{u.latest:<{widths[2]}}  {u.location:<{widths[3]}}  {u.dep_group}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    python, npm = find_components()
    updates: list[Update] = []
    for path in python:
        print(f"Checking {label(path, 'python')}...", file=sys.stderr)
        updates.extend(python_updates(path))
    for path in npm:
        print(f"Checking {label(path, 'npm')}...", file=sys.stderr)
        updates.extend(npm_updates(path))

    if args.json:
        print(json.dumps([asdict(u) for u in updates], indent=2))
        return 0

    by_type = {
        t: [u for u in updates if u.update_type == t]
        for t in ("major", "minor", "patch")
    }
    for update_type, group in by_type.items():
        print(f"\n{'=' * 60}")
        print(f"{update_type.upper()} updates ({len(group)})")
        print("=" * 60)
        print_table(group)
    counts = ", ".join(f"{len(group)} {t}" for t, group in by_type.items())
    print(f"\nTotal: {len(updates)} updates ({counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
