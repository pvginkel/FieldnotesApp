"""Run all code quality checks: ruff, mypy, vulture, and pytest."""

import subprocess
import sys

CHECKS = [
    ("ruff", ["ruff", "check", "."]),
    ("mypy", ["mypy", "."]),
    ("vulture", ["vulture", "app/", "vulture_whitelist.py", "--min-confidence", "80"]),
    ("pytest", ["pytest"]),
]


def main() -> None:
    failed: list[str] = []

    for name, cmd in CHECKS:
        print(f"\n{'=' * 60}")
        print(f"  Running {name}")
        print(f"{'=' * 60}\n")

        result = subprocess.run(cmd)
        if result.returncode != 0:
            failed.append(name)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"  FAILED: {', '.join(failed)}")
        print(f"{'=' * 60}")
        sys.exit(1)
    else:
        print("  All checks passed.")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
