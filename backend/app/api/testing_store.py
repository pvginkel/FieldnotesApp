"""The Playwright suite's store, under /api/testing/store: only in testing mode.

The frontend's Playwright backend serves an empty store of its own (`FieldnotesService`), one per
worker, and every test that opens the triage page starts by laying out the store it needs:
`PUT` replaces every file under `observations/` and `triage/` with the ones given, as a skill's
push would, and answers once the triage index and the observation index have taken it in. `GET`
answers those files as the store holds them, for a test to read what the UI wrote. Both go through
the store's write queue, so they never land between a write's fetch and its push. The files are
the test's own and are not checked: an item the triage index refuses is logged and left out of the
queue, as it is in production.

Not in the OpenAPI document: it is no part of the app's surface.
"""

from pathlib import Path
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, jsonify, request

from app.api.testing_guard import reject_if_not_testing
from app.fieldnotes.store import Commit
from app.services.container import ServiceContainer
from app.services.fieldnotes_service import FieldnotesService

testing_store_bp = Blueprint("testing_store", __name__, url_prefix="/api/testing/store")

# What a test lays out; the rest of the store (the skills, a README) is none of its business.
DIRECTORIES = ("observations", "triage")


@testing_store_bp.before_request
def check_testing_mode() -> Any:
    return reject_if_not_testing()


def _files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text()
        for directory in DIRECTORIES
        if (root / directory).is_dir()
        for path in sorted((root / directory).rglob("*"))
        if path.is_file()
    }


@testing_store_bp.route("", methods=["GET"])
@inject
def read_store(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """The files under `observations/` and `triage/`, by path."""
    service.ready_observations()
    assert service.runtime is not None
    files = service.runtime.store.write(lambda root: (_files(root), None))
    return jsonify({"files": files})


@testing_store_bp.route("", methods=["PUT"])
@inject
def replace_store(
    service: FieldnotesService = Provide[ServiceContainer.fieldnotes_service],
) -> Any:
    """`{"files": {path: text}}`: the store's `observations/` and `triage/` become these files,
    in one commit, pushed and indexed before the reply."""
    body = request.get_json(silent=True)
    files = body.get("files") if isinstance(body, dict) else None
    if not isinstance(files, dict) or not all(
        isinstance(path, str) and path.split("/")[0] in DIRECTORIES and isinstance(text, str)
        for path, text in files.items()
    ):
        return jsonify(
            {"message": 'expected {"files": {path: text}}, every path under observations/ or triage/'}
        ), 400
    service.ready_observations()
    assert service.runtime is not None

    def edit(root: Path) -> tuple[None, Commit | None]:
        old = _files(root)
        changed = {path for path in old.keys() | files.keys() if old.get(path) != files.get(path)}
        for path in changed:
            target = root / path
            if path in files:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(files[path])
            else:
                target.unlink()
        if not changed:
            return None, None
        return None, Commit(tuple(sorted(changed)), "the store laid out (Playwright)")

    service.runtime.store.write(edit)
    return "", 204
