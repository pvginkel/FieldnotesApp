"""The frontend's committed OpenAPI cache matches the backend's document.

The frontend generates its API client from frontend/openapi-cache/openapi.json,
a committed copy of this backend's /api/docs/openapi.json. An API change that
is committed without refreshing the cache leaves the client describing the old
API, and nothing else fails. This test does.

It runs where the backend sits beside a frontend, as in the app's monorepo
(the suite runner's backend suite and CI's included), and skips elsewhere.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_CACHE_FILE = _BACKEND_DIR.parent / "frontend" / "openapi-cache" / "openapi.json"

# Spectree documents only the first app a process creates, and the suite
# creates many, so the document comes from an app built in a fresh process.
# It goes to a file: the app may log to stdout or stderr while it starts.
_DUMP_DOCUMENT = """
import json
import sys

from app import create_app

app = create_app(skip_background_services=True)
response = app.test_client().get("/api/docs/openapi.json")
if response.status_code != 200:
    sys.exit(f"GET /api/docs/openapi.json answered {response.status_code}")
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(response.get_json(), f)
"""


def _differences(cached: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Name what differs, so the failure says more than "stale"."""
    lines: list[str] = []
    cached_paths = cached.get("paths", {})
    current_paths = current.get("paths", {})
    for path in sorted(current_paths.keys() - cached_paths.keys()):
        lines.append(f"  not in the cache: {path}")
    for path in sorted(cached_paths.keys() - current_paths.keys()):
        lines.append(f"  no longer in the backend: {path}")
    for path in sorted(cached_paths.keys() & current_paths.keys()):
        if cached_paths[path] != current_paths[path]:
            lines.append(f"  changed: {path}")
    for key in sorted((cached.keys() | current.keys()) - {"paths"}):
        if cached.get(key) != current.get(key):
            lines.append(f"  changed: the document's {key!r}")
    return lines


def test_frontend_openapi_cache_matches_the_backend(tmp_path: Path) -> None:
    if not _CACHE_FILE.exists():
        pytest.skip(f"no frontend OpenAPI cache beside the backend ({_CACHE_FILE})")

    document_file = tmp_path / "openapi.json"
    result = subprocess.run(
        [sys.executable, "-c", _DUMP_DOCUMENT, str(document_file)],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"Building the app for its OpenAPI document failed:\n{result.stderr[-4000:]}"
    )

    current = json.loads(document_file.read_text(encoding="utf-8"))
    cached = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))

    if cached != current:
        pytest.fail(
            "frontend/openapi-cache/openapi.json does not match the backend's OpenAPI "
            "document. Run scripts/regenerate-openapi.py --frontend from the repository "
            "root, and commit the cache with the regenerated client.\n"
            + "\n".join(_differences(cached, current)),
            pytrace=False,
        )
