"""The triage index and `GET /api/triage/queue` (FR-23), the operator's writes (FR-24 to FR-26),
all behind the operator's `editor` gate (NFR-4), and the triage metrics. Every item is invented,
spelled as the store's writers spell it."""

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.fieldnotes.document import Document
from app.fieldnotes.index import observation_path
from app.fieldnotes.store import Commit
from app.fieldnotes.triage import ITEM_KEYS, Verb, item_faults, item_path

IDS = [f"01JA{n:022d}" for n in range(10)]
WRITTEN = "2026-09-20T09:00:00Z"
NOW = datetime(2026, 9, 24, 8, 0, 0, tzinfo=UTC)
AT = "2026-09-24T08:00:00Z"
TEXT = "uv sync installs no workspace members"
REPO = "pvginkel/Example"
QUESTION = {
    "at": "2026-09-22T07:00:00Z",
    "text": "Which repository holds the setup verb?",
}


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


def item(id_, written=WRITTEN, **fields):
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
    remote.push(
        {item_path(i["observation"]): spelled(i) for i in items} | files, "reconcile"
    )


def ids(reply):
    return [i["observation"] for i in reply["items"]]


def queue(api, **kwargs):
    response = api.get("/api/triage/queue", **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def post(api, auth, text=TEXT):
    body = {"area": "uv", "category": "hint", "text": text, "repo": REPO, "force": True}
    return api.post("/api/observations", json=body, headers=auth()).json()["id"]


def rule(api, id_, verb="yes", note="", written=WRITTEN, **kwargs):
    body = {"verb": verb, "note": note, "written": written}
    return api.put(f"/api/triage/items/{id_}/ruling", json=body, **kwargs)


def take_back(api, id_, written=WRITTEN, **kwargs):
    url = f"/api/triage/items/{id_}/ruling"
    return api.delete(url, query_string={"written": written}, **kwargs)


def submit(api, **kwargs):
    return api.post("/api/triage/submit", **kwargs)


def stored(remote, id_):
    return json.loads(remote.file(item_path(id_)))


def touched(remote):
    """The paths the remote's last commit changed."""
    show = ["git", "show", "--name-only", "--format=", "main"]
    return subprocess.run(
        show, cwd=remote.path, check=True, capture_output=True, text=True
    ).stdout.split()


def refused(response, status, type_):
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == type_


def test_the_queue_is_every_item_not_submitted_returned_first_then_by_written(
    start, remote
):
    seed(
        remote,
        item(IDS[0], "2026-09-22T09:00:00Z"),
        item(
            IDS[1],
            "2026-09-20T09:00:00Z",
            ruling=ruling("later", "after the next release"),
        ),
        item(
            IDS[2],
            "2026-09-19T09:00:00Z",
            ruling=ruling(submitted="2026-09-21T09:00:00Z"),
        ),
        item(IDS[3], "2026-09-23T09:00:00Z", ruling=ruling(), question=QUESTION),
        item(
            IDS[4],
            "2026-09-21T09:00:00Z",
            ruling=ruling("no", "not ours"),
            question=QUESTION,
        ),
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
    returned = item(
        IDS[0], ruling=ruling("no", "not ours: KubeCoder's"), question=QUESTION
    )
    seed(remote, returned)
    with start() as api:
        response = api.get("/api/triage/queue")

    assert list(response.json()) == ["items", "observations"]
    [served] = response.json()["items"]
    assert served == returned
    assert list(served) == list(ITEM_KEYS)


def test_the_observations_are_as_the_store_has_them_now(
    start, auth, remote, clock, pull
):
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
            (root / item_path(IDS[0])).write_text(
                spelled(item(IDS[0], ruling=ruling()))
            )
            return None, Commit((item_path(IDS[0]),), "rule")

        api.runtime.store.write(write)
        [written] = queue(api)["items"]

    assert first == [IDS[0], IDS[1]]
    assert pulled == [IDS[0], IDS[2]]
    assert withdrawn == [IDS[0]]
    assert written["ruling"] == ruling()


def test_a_pull_reaches_the_queue_while_the_models_pod_is_down(
    start, auth, remote, models
):
    with start() as api:
        id_ = post(api, auth)
        path = observation_path(id_)
        rewritten = Document(remote.file(path)).with_fields(
            canonical="a statement rewritten"
        )
        remote.push(
            {path: rewritten.text, item_path(id_): spelled(item(id_))}, "reconcile"
        )
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


def test_the_queue_admits_the_editor_session(
    start, remote, oidc_app, generate_test_jwt
):
    seed(remote, item(IDS[0]))
    with start(app=oidc_app) as api:
        api.sign_in(generate_test_jwt(roles=["editor"]))
        reply = queue(api)

    assert ids(reply) == [IDS[0]]


def test_the_queue_refuses_a_user_without_the_editor_role(
    start, oidc_app, generate_test_jwt
):
    with start(app=oidc_app) as api:
        api.sign_in(generate_test_jwt(roles=["viewer"]))
        response = api.get("/api/triage/queue")

    assert response.status_code == 403


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer mcp-token"}])
def test_the_queue_refuses_no_session_and_an_agents_bearer(start, oidc_app, headers):
    with start(app=oidc_app) as api:
        response = api.get("/api/triage/queue", headers=headers)

    assert response.status_code == 401


def test_the_triage_endpoints_are_in_the_openapi_document_behind_the_editor_gate():
    """In a process of its own: the views are decorated by the first app's Spectree instance, and a
    later app's document leaves out the routes another instance decorated."""
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

    def schema(content):
        ref = content["application/json"]["schema"]["$ref"]
        return document["components"]["schemas"][ref.rsplit("/", 1)[-1]]

    paths = document["paths"]
    operations = {
        "queue": paths["/api/triage/queue"]["get"],
        "rule": paths["/api/triage/items/{id}/ruling"]["put"],
        "take back": paths["/api/triage/items/{id}/ruling"]["delete"],
        "submit": paths["/api/triage/submit"]["post"],
    }
    replies = {
        name: schema(o["responses"]["200"]["content"]) for name, o in operations.items()
    }

    assert {o["x-required-role"] for o in operations.values()} == {"editor"}
    assert set(replies["queue"]["properties"]) == {"items", "observations"}
    assert set(replies["rule"]["properties"]) == set(ITEM_KEYS)
    assert set(replies["take back"]["properties"]) == set(ITEM_KEYS)
    assert replies["submit"]["type"] == "array"
    request = schema(operations["rule"]["requestBody"]["content"])
    assert set(request["properties"]) == {"verb", "note", "written"}
    query = [
        p["name"] for p in operations["take back"]["parameters"] if p["in"] == "query"
    ]
    assert query == ["written"]


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


# -- the operator's writes (FR-24 to FR-26) ------------------------------------------------------


def test_a_ruling_is_one_commit_of_the_ruling_alone(start, remote, clock):
    seed(remote, item(IDS[0]), item(IDS[1]))
    clock.now = NOW
    with start() as api:
        before = remote.log()
        response = rule(api, IDS[0], "no", "  not ours: KubeCoder's  ")
        queued = queue(api)["items"][0]

    expected = item(IDS[0], ruling=ruling("no", "not ours: KubeCoder's", at=AT))
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert remote.log() == [f"rule {IDS[0]} no (operator)", *before]
    assert touched(remote) == [item_path(IDS[0])]
    # Spelled as the store's writers spell it, and nothing but the ruling changed.
    assert remote.file(item_path(IDS[0])) == spelled(expected)
    assert item_faults(f"{IDS[0]}.json", stored(remote, IDS[0])) == []
    assert queued == expected


def test_a_ruling_replaces_a_draft_and_a_returned_item_keeps_its_question(
    start, remote, clock
):
    seed(remote, item(IDS[0], ruling=ruling("no", "not ours"), question=QUESTION))
    clock.now = NOW
    with start() as api:
        response = rule(api, IDS[0], "yes")

    assert response.json()["ruling"] == ruling("yes", "", at=AT)
    assert response.json()["question"] == QUESTION
    assert stored(remote, IDS[0]) == response.json()


@pytest.mark.parametrize("verb", ["no", "later"])
@pytest.mark.parametrize("note", ["", "   "])
def test_a_no_or_a_later_without_a_note_is_refused(start, remote, verb, note):
    seed(remote, item(IDS[0]))
    with start() as api:
        before = remote.log()
        response = rule(api, IDS[0], verb, note)

    refused(response, 422, "validation-error")
    assert response.json()["errors"][0]["loc"] == ["body"]
    assert remote.log() == before


@pytest.mark.parametrize(
    ("id_", "body", "loc"),
    [
        ("not-a-ulid", {}, ["path", "id"]),
        (IDS[0], {"verb": "maybe"}, ["body", "verb"]),
        (IDS[0], {"written": "2026-09-20 09:00"}, ["body", "written"]),
        (IDS[0], {"written": None}, ["body", "written"]),
        (IDS[0], {"extra": 1}, ["body", "extra"]),
    ],
)
def test_a_ruling_that_does_not_validate_is_refused(start, remote, id_, body, loc):
    seed(remote, item(IDS[0]))
    valid = {"verb": "yes", "note": "", "written": WRITTEN}
    with start() as api:
        before = remote.log()
        response = api.put(f"/api/triage/items/{id_}/ruling", json=valid | body)

    refused(response, 422, "validation-error")
    assert [e["loc"] for e in response.json()["errors"]] == [loc]
    assert remote.log() == before


def test_a_take_back_without_written_is_refused(start, remote):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        response = api.delete(f"/api/triage/items/{IDS[0]}/ruling")

    refused(response, 422, "validation-error")
    assert [e["loc"] for e in response.json()["errors"]] == [["query", "written"]]


WRITES = {
    "rule": lambda api, id_, written=WRITTEN: rule(
        api, id_, "later", "next week", written
    ),
    "take back": lambda api, id_, written=WRITTEN: take_back(api, id_, written),
}


@pytest.mark.parametrize("write", WRITES.values(), ids=list(WRITES))
@pytest.mark.parametrize(
    ("seeded", "written", "status", "type_"),
    [
        (
            item(IDS[0], ruling=ruling(submitted="2026-09-21T09:00:00Z")),
            WRITTEN,
            409,
            "conflict",
        ),
        (item(IDS[1]), WRITTEN, 404, "not-found"),
        (item(IDS[0], ruling=ruling()), "2026-09-19T09:00:00Z", 409, "conflict"),
    ],
    ids=["submitted", "missing", "rewritten"],
)
def test_a_ruling_and_a_take_back_refuse_and_write_nothing(
    start, remote, write, seeded, written, status, type_
):
    seed(remote, seeded)
    with start() as api:
        before = remote.log()
        response = write(api, IDS[0], written)

    refused(response, status, type_)
    assert remote.log() == before


def test_the_refusals_are_decided_at_the_stores_tip(start, remote):
    seed(remote, item(IDS[0]), item(IDS[1]))
    with start() as api:
        # A skill's push the API has not pulled: the queue still holds the items as they were.
        remote.push(
            {
                item_path(IDS[0]): spelled(item(IDS[0], "2026-09-23T09:00:00Z")),
                item_path(IDS[1]): None,
                item_path(IDS[2]): spelled(item(IDS[2])),
            },
            "reconcile",
        )
        stale = ids(queue(api))
        rewritten = rule(api, IDS[0])
        withdrawn = rule(api, IDS[1])
        added = rule(api, IDS[2])

    assert stale == [IDS[0], IDS[1]]
    refused(rewritten, 409, "conflict")
    refused(withdrawn, 404, "not-found")
    assert added.status_code == 200, added.text


def test_a_take_back_clears_the_draft_in_one_commit(start, remote):
    seed(
        remote,
        item(IDS[0], ruling=ruling("later", "after the release")),
        item(IDS[1], ruling=ruling("no", "not ours"), question=QUESTION),
    )
    with start() as api:
        before = remote.log()
        cleared = take_back(api, IDS[0])
        returned = take_back(api, IDS[1])

    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == item(IDS[0])
    assert returned.json() == item(IDS[1], question=QUESTION)
    assert remote.log() == [
        f"unrule {IDS[1]} (operator)",
        f"unrule {IDS[0]} (operator)",
        *before,
    ]
    assert touched(remote) == [item_path(IDS[1])]
    for id_, expected in ((IDS[0], cleared), (IDS[1], returned)):
        assert remote.file(item_path(id_)) == spelled(expected.json())
        assert item_faults(f"{id_}.json", stored(remote, id_)) == []


def test_a_take_back_without_a_ruling_writes_nothing(start, remote):
    seed(remote, item(IDS[0]))
    with start() as api:
        before = remote.log()
        response = take_back(api, IDS[0])

    assert response.status_code == 200, response.text
    assert response.json() == item(IDS[0])
    assert remote.log() == before


def test_submit_marks_every_ruled_item_submitted_in_one_commit(start, remote, clock):
    later = ruling("later", "after the release")
    again = ruling("yes", at="2026-09-23T08:00:00Z")
    seeded = [
        item(IDS[0]),
        item(IDS[1], ruling=ruling()),
        item(IDS[2], ruling=later),
        item(IDS[3], ruling=ruling(submitted="2026-09-21T09:00:00Z")),
        # Returned, and ruled before its question: not ruled again yet.
        item(
            IDS[4],
            "2026-09-19T09:00:00Z",
            ruling=ruling("no", "not ours"),
            question=QUESTION,
        ),
        # Returned, and ruled again after its question.
        item(IDS[5], ruling=again, question=QUESTION),
    ]
    seed(remote, *seeded, **{item_path(IDS[6]): "{"})
    clock.now = NOW
    with start() as api:
        before = remote.log()
        response = submit(api)
        left = ids(queue(api))

    assert response.status_code == 200, response.text
    assert response.json() == [IDS[1], IDS[2], IDS[5]]
    assert remote.log() == ["submit 3 (operator)", *before]
    assert touched(remote) == [item_path(IDS[n]) for n in (1, 2, 5)]
    for n, original in enumerate(seeded):
        expected = original
        if n in (1, 2, 5):
            expected = original | {"ruling": original["ruling"] | {"submitted": AT}}
        assert remote.file(item_path(IDS[n])) == spelled(expected)
        assert item_faults(f"{IDS[n]}.json", expected) == []
    assert left == [IDS[4], IDS[0]]


def test_submit_with_nothing_ruled_writes_nothing(start, remote):
    seed(remote, item(IDS[0]), item(IDS[1], ruling=ruling(), question=QUESTION))
    with start() as api:
        before = remote.log()
        response = submit(api)

    assert response.status_code == 200, response.text
    assert response.json() == []
    assert remote.log() == before


WRITES = {
    "rule": lambda api: rule(api, IDS[0], "no", "not ours"),
    "take back": lambda api: take_back(api, IDS[0]),
    "submit": submit,
}


@pytest.mark.parametrize("write", WRITES.values(), ids=list(WRITES))
def test_a_write_the_store_refuses_is_the_reply(start, remote, write):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start() as api:
        before = remote.log()
        hook = remote.path / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        response = write(api)
        queued = queue(api)["items"]

    refused(response, 502, "store-unreachable")
    assert remote.log() == before
    assert queued == [item(IDS[0], ruling=ruling())]


@pytest.mark.parametrize("write", WRITES.values(), ids=list(WRITES))
def test_a_write_the_index_cannot_follow_is_store_unreachable(
    start, auth, remote, models, write
):
    """A write brings the observation index up to the tip before its edit, and the index embeds a
    rewritten observation with the models pod: while the pod is down, the write fails (FR-24)."""
    with start() as api:
        id_ = post(api, auth)
        path = observation_path(id_)
        rewritten = Document(remote.file(path)).with_fields(
            canonical="a statement rewritten"
        )
        seed(remote, item(IDS[0], ruling=ruling()), **{path: rewritten.text})
        models.fail = True
        before = remote.log()
        response = write(api)

    refused(response, 502, "store-unreachable")
    assert remote.log() == before


# -- the writes' gate (NFR-4) --------------------------------------------------------------------

GATED = {
    "rule": lambda api, **kwargs: rule(api, IDS[0], **kwargs),
    "take back": lambda api, **kwargs: take_back(api, IDS[0], **kwargs),
    "submit": submit,
}


@pytest.mark.parametrize("write", GATED.values(), ids=list(GATED))
def test_the_writes_admit_the_editor_session(
    start, remote, oidc_app, generate_test_jwt, write
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start(app=oidc_app) as api:
        api.sign_in(generate_test_jwt(roles=["editor"]))
        response = write(api)

    assert response.status_code == 200, response.text


@pytest.mark.parametrize("write", GATED.values(), ids=list(GATED))
@pytest.mark.parametrize(
    ("roles", "headers", "status"),
    [
        (["viewer"], {}, 403),
        (None, {}, 401),
        (None, {"Authorization": "Bearer mcp-token"}, 401),
    ],
    ids=["no role", "no session", "an agent's bearer"],
)
def test_the_writes_refuse_all_but_the_editor(
    start, remote, oidc_app, generate_test_jwt, write, roles, headers, status
):
    seed(remote, item(IDS[0], ruling=ruling()))
    with start(app=oidc_app) as api:
        before = remote.log()
        if roles is not None:
            api.sign_in(generate_test_jwt(roles=roles))
        response = write(api, headers=headers)

    assert response.status_code == status
    assert remote.log() == before


# -- the rulings' counter ------------------------------------------------------------------------


def test_the_rulings_are_counted_by_verb(start, remote, scrape):
    seed(remote, item(IDS[0]), item(IDS[1]))
    with start() as api:
        before = scrape(api)
        rule(api, IDS[0], "no", "not ours")
        rule(api, IDS[0], "later", "after the release")
        rule(api, IDS[1], "later", "after the release")
        rule(api, IDS[1], "no", "")  # refused: not a ruling
        take_back(api, IDS[1])
        after = scrape(api)

    name = "fieldnotes_triage_rulings_total"
    assert [before(name, verb=v) for v in Verb] == [0, 0, 0]
    assert {v: after(name, verb=v) for v in Verb} == {"yes": 0, "no": 1, "later": 2}
