"""The triage index and `GET /api/triage/queue` (FR-23), behind the operator's `editor` gate
(NFR-4), and the queue's gauge. Every item is invented, spelled as the store's writers spell it."""

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from app.fieldnotes.document import Document
from app.fieldnotes.index import observation_path
from app.fieldnotes.store import Commit
from app.fieldnotes.triage import ITEM_KEYS, item_path

IDS = [f"01JA{n:022d}" for n in range(10)]
TEXT = "uv sync installs no workspace members"
REPO = "pvginkel/Example"
QUESTION = {"at": "2026-09-22T07:00:00Z", "text": "Which repository holds the setup verb?"}


def snapshot(**fields):
    """An observation's fields as the reconciler copies them from its frontmatter."""
    return {
        "status": "open",
        "category": "hint",
        "area": "uv",
        "canonical": TEXT,
        "repos": [REPO],
        "card": None,
        "outcome": None,
        "reason": None,
        "created": "2026-09-19T10:00:00Z",
        "last_seen": "2026-09-19T10:00:00Z",
    } | fields


def item(id_, written="2026-09-20T09:00:00Z", **fields):
    return {
        "observation": id_,
        "written": written,
        "headline": "uv sync leaves the workspace members out",
        "ask": "Document `--all-packages` beside the setup verb?",
        "evidence": "2 reactions (📝 2) from 1 repo: pvginkel/Example.",
        "recommendation": "Yes: the setup verb passes it already.",
        "impact": "Each session that meets it syncs twice.",
        "snapshot": snapshot(),
        "reports": [
            {
                "at": "2026-09-19T10:00:00Z",
                "emoji": "📝",
                "repo": REPO,
                "session": "s-1",
                "client": "mcp",
                "text": TEXT,
                "new": False,
            }
        ],
        "ruling": None,
        "question": None,
        "actioned": None,
    } | fields


def ruling(verb="yes", note="", at="2026-09-21T08:00:00Z", submitted=None):
    return {"verb": verb, "note": note, "at": at, "submitted": submitted}


def spelled(value) -> str:
    """As the store's writers write an item: two spaces, UTF-8 as is, a trailing newline."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def seed(remote, *items, **files):
    """Push the items to `triage/<id>.json`, and any other files by path."""
    remote.push({item_path(i["observation"]): spelled(i) for i in items} | files, "reconcile")


def ids(reply):
    return [i["observation"] for i in reply["items"]]


def queue(api, **kwargs):
    response = api.get("/api/triage/queue", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def post(api, auth, text=TEXT):
    body = {"area": "uv", "category": "hint", "text": text, "repo": REPO, "force": True}
    return api.post("/api/observations", json=body, headers=auth()).json()["id"]


def test_the_queue_is_every_item_not_submitted_returned_first_then_by_written(start, remote):
    seed(
        remote,
        item(IDS[0], "2026-09-22T09:00:00Z"),
        item(IDS[1], "2026-09-20T09:00:00Z", ruling=ruling("later", "after the next release")),
        item(IDS[2], "2026-09-19T09:00:00Z", ruling=ruling(submitted="2026-09-21T09:00:00Z")),
        item(IDS[3], "2026-09-23T09:00:00Z", ruling=ruling(), question=QUESTION),
        item(IDS[4], "2026-09-21T09:00:00Z", ruling=ruling("no", "not ours"), question=QUESTION),
        item(
            IDS[5],
            ruling=ruling(submitted="2026-09-22T08:00:00Z"),
            question=QUESTION | {"at": "2026-09-21T09:00:00Z"},
        ),
        **{
            f"triage/done/2026-09-18-{IDS[6]}.json": spelled(
                item(IDS[6], "2026-09-18T09:00:00Z", actioned={"at": "x", "done": "y"})
            ),
            f"triage/archive/{IDS[7]}.json": spelled(item(IDS[7])),
        },
    )
    with start() as api:
        reply = queue(api)

    # Returned (a question, not submitted since) by `written`, then the rest by `written`; the
    # submitted, the answered question submitted again, and done/ and archive/ are not the queue.
    assert ids(reply) == [IDS[4], IDS[3], IDS[1], IDS[0]]


def test_an_item_comes_as_the_store_holds_it(start, remote):
    returned = item(IDS[0], ruling=ruling("no", "not ours: KubeCoder's"), question=QUESTION)
    seed(remote, returned)
    with start() as api:
        response = api.get("/api/triage/queue")

    assert list(response.json()) == ["items", "observations"]
    [served] = response.json()["items"]
    assert served == returned
    assert list(served) == list(ITEM_KEYS)


def test_the_observations_are_as_the_store_has_them_now(start, auth, remote, clock, pull):
    with start() as api:
        same = post(api, auth)
        changed = post(api, auth, "the relay drops deliveries")
        clock.tick()
        api.post(
            f"/api/observations/{changed}/reactions",
            json={"emoji": "👍", "repo": "pvginkel/Other"},
            headers=auth(),
        )
        seed(
            remote,
            item(same),
            item(changed, snapshot=snapshot(canonical="the relay drops deliveries")),
            item(IDS[0]),
        )
        pull(api)
        observations = queue(api)["observations"]

    # Unchanged since the item was written: equal to its snapshot, field for field, as strings.
    assert observations[same] == snapshot()
    assert observations[changed] == snapshot(
        canonical="the relay drops deliveries",
        repos=[REPO, "pvginkel/Other"],
        last_seen="2026-09-19T10:01:00Z",
    )
    assert observations[IDS[0]] is None  # merged away or deleted
    assert set(observations) == {same, changed, IDS[0]}


def test_a_bad_item_is_left_out_and_logged(start, remote, caplog):
    broken = item(IDS[5])
    del broken["snapshot"]["status"]
    seed(
        remote,
        item(IDS[1], ruling=ruling("maybe")),
        item(IDS[2], actioned={"at": "2026-09-22T09:00:00Z", "done": "closed"}),
        broken,
        item(IDS[6]),
        **{
            item_path(IDS[0]): "{",
            item_path(IDS[3]): spelled(item(IDS[4])),
        },
    )
    with caplog.at_level(logging.ERROR, logger="app.fieldnotes.triage"), start() as api:
        reply = queue(api)

    assert ids(reply) == [IDS[6]]
    logged = "\n".join(caplog.messages)
    for id_ in (IDS[0], IDS[1], IDS[2], IDS[3], IDS[5]):
        assert f"{item_path(id_)} is left out of the triage queue" in logged
    assert "the ruling's verb is not one of yes, no, later" in logged
    assert "a stamped item is still open" in logged
    assert f"the file name is not {IDS[4]}.json" in logged


def test_the_queue_follows_pulls_and_writes(start, remote, pull):
    seed(remote, item(IDS[0]), item(IDS[1]))
    with start() as api:
        first = ids(queue(api))
        seed(
            remote,
            item(IDS[1], ruling=ruling(submitted="2026-09-21T09:00:00Z")),
            item(IDS[2]),
        )
        pull(api)
        pulled = ids(queue(api))
        remote.push({item_path(IDS[2]): None}, "withdraw")
        pull(api)
        withdrawn = ids(queue(api))

        def write(root: Path):
            (root / item_path(IDS[0])).write_text(spelled(item(IDS[0], ruling=ruling())))
            return None, Commit((item_path(IDS[0]),), "rule")

        api.runtime.store.write(write)
        [written] = queue(api)["items"]

    assert first == [IDS[0], IDS[1]]
    assert pulled == [IDS[0], IDS[2]]
    assert withdrawn == [IDS[0]]
    assert written["ruling"] == ruling()


def test_a_pull_reaches_the_queue_while_the_models_pod_is_down(start, auth, remote, models):
    with start() as api:
        id_ = post(api, auth)
        path = observation_path(id_)
        rewritten = Document(remote.file(path)).with_fields(canonical="a statement rewritten")
        remote.push({path: rewritten.text, item_path(id_): spelled(item(id_))}, "reconcile")
        models.fail = True

        failed = api.runtime.store.pull().exception()
        reply = queue(api)

    assert failed is not None
    assert ids(reply) == [id_]


def test_the_queue_is_not_ready_until_the_store_is(start):
    with start(ready=False, FIELDNOTES_STORE_URL="/nonexistent/remote.git") as api:
        api.runtime.wait_started(20)
        response = api.get("/api/triage/queue")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "not-ready"


# -- the operator's gate (NFR-4) -----------------------------------------------------------------


def test_the_queue_admits_the_editor_session(start, remote, oidc_app, generate_test_jwt):
    seed(remote, item(IDS[0]))
    with start(app=oidc_app) as api:
        api.sign_in(generate_test_jwt(roles=["editor"]))
        reply = queue(api)

    assert ids(reply) == [IDS[0]]


def test_the_queue_refuses_a_user_without_the_editor_role(start, oidc_app, generate_test_jwt):
    with start(app=oidc_app) as api:
        api.sign_in(generate_test_jwt(roles=["viewer"]))
        response = api.get("/api/triage/queue")

    assert response.status_code == 403


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer mcp-token"}])
def test_the_queue_refuses_no_session_and_an_agents_bearer(start, oidc_app, headers):
    with start(app=oidc_app) as api:
        response = api.get("/api/triage/queue", headers=headers)

    assert response.status_code == 401


def test_the_queue_is_in_the_openapi_document_behind_the_editor_gate():
    """In a process of its own: the document is built by the first app a process creates, and
    names only the routes its own Spectree instance decorated."""
    script = (
        "import json\n"
        "from app import create_app\n"
        "from tests.conftest_infrastructure import _build_test_app_settings,"
        " _build_test_settings\n"
        "app = create_app(_build_test_settings(), app_settings=_build_test_app_settings(),"
        " skip_background_services=True)\n"
        "print(json.dumps(app.test_client().get('/api/docs/openapi.json').get_json()))\n"
    )
    backend = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    document = json.loads(run.stdout.splitlines()[-1])

    operation = document["paths"]["/api/triage/queue"]["get"]
    assert operation["x-required-role"] == "editor"
    reply = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema = document["components"]["schemas"][reply.rsplit("/", 1)[-1]]
    assert set(schema["properties"]) == {"items", "observations"}


# -- the gauge -----------------------------------------------------------------------------------


def test_the_gauge_counts_the_queue_at_scrape_time(start, remote, pull, scrape):
    seed(
        remote,
        item(IDS[0]),
        item(IDS[1], ruling=ruling()),
        item(IDS[2], ruling=ruling(submitted="2026-09-21T09:00:00Z")),
    )
    with start() as api:
        before = scrape(api)("fieldnotes_triage_queue")
        remote.push({item_path(IDS[0]): None}, "withdraw")
        pull(api)
        after = scrape(api)("fieldnotes_triage_queue")

    assert (before, after) == (2, 1)
