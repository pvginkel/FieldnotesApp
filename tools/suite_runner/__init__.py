"""Suite runner — test orchestration for the FieldnotesApp monorepo."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
RESULTS_FILE = REPO_ROOT / "test_results.md"
ALL_SUITES = ["backend", "frontend"]
