"""The REST endpoints (design, "Services"): post (FR-1..FR-3), react (FR-4), get (FR-5), match,
neighbors, the health checks, bearer auth (NFR-4) and the problem+json errors."""

from datetime import UTC, datetime

import pytest

from app.fieldnotes.document import Document, new_document
from app.fieldnotes.index import observation_path
from fieldnotes_contracts import Reaction

DUPLICATE = "uv sync installs no workspace members"
REWRITTEN = "uv sync leaves the workspace members out"


def post(api, auth, text=DUPLICATE, area="uv", **fields):
    body = {"area": area, "category": "hint", "text": text, "repo": "pvginkel/Example"}
    return api.post("/api/observations", json=body | fields, headers=auth())


def indexed_commit(api, auth):
    reply = api.post("/api/match", json={"text": DUPLICATE}, headers=auth("skills"))
    return reply.json()["indexed_commit"]


def rewrite(remote, id_, canonical=REWRITTEN):
    """A reconciler's push rewriting the observation's statement; the new commit."""
    path = observation_path(id_)
    rewritten = Document(remote.file(path)).with_fields(canonical=canonical)
    return remote.push({path: rewritten.text}, "reconciler: rewrite")


def test_health_answers_without_a_token(start):
    with start() as api:
        assert api.get("/health/healthz").status_code == 200
        # The template's readiness also waits on the SSE gateway, which no suite runs.
        assert api.get("/health/readyz").json()["store"] == {
            "ok": True,
            "failed": False,
        }


def test_a_failed_start_keeps_the_store_unready(start, auth):
    """In production the failure also ends the process, so the pod is restarted
    (`FieldnotesService`); here the runtime has no `on_failure`."""
    with start(ready=False, FIELDNOTES_STORE_URL="/nonexistent/remote.git") as api:
        api.runtime.wait_started(20)
        assert api.get("/health/readyz").json()["store"] == {
            "ok": False,
            "failed": True,
        }
        problem = post(api, auth).json()
        assert (problem["status"], problem["type"]) == (503, "not-ready")


def test_a_start_that_cannot_index_the_checkout_fails(start, remote, models):
    """A write never waits on the observation index, but the start does: with the models pod
    down and a text missing from the cache the store never becomes ready, and in production the
    process then exits to be restarted."""
    id_ = "01K5H8ZQ3V6D9W2X4Y7B1C0E5F"
    at = datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC)
    reaction = Reaction(
        at=at, emoji="📝", repo="pvginkel/Example", client="mcp", text=DUPLICATE
    )
    document = new_document(
        id_=id_, area="uv", category="hint", text=DUPLICATE, reaction=reaction
    )
    remote.push({observation_path(id_): document.text}, "a skill's post")
    models.fail = True

    with start(ready=False) as api:
        api.runtime.wait_started(20)
        store = api.get("/health/readyz").json()["store"]

    assert store == {"ok": False, "failed": True}


@pytest.mark.parametrize(
    "header",
    [None, "Bearer wrong", "Basic bWNwLXRva2Vu", "Bearer", "mcp-token"],
)
def test_a_request_without_a_known_bearer_is_refused(start, header):
    with start() as api:
        headers = {"Authorization": header} if header else {}
        response = api.get(f"/api/observations/{'0' * 26}", headers=headers)
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "unauthenticated"


def test_a_novel_post_creates_an_observation(start, auth, remote):
    with start() as api:
        response = post(api, auth, session="s-1")
        id_ = response.json()["id"]
        observation = api.get(f"/api/observations/{id_}", headers=auth()).json()

    assert response.status_code == 201
    assert response.json() == {"id": id_, "candidates": []}
    assert remote.files() == [observation_path(id_)]
    assert remote.log() == [f"post {id_} (mcp): uv"]
    assert observation["status"] == "open"
    assert observation["canonical"] == DUPLICATE
    assert observation["repos"] == ["pvginkel/Example"]
    assert observation["created"] == "2026-09-19T10:00:00Z"
    assert observation["reactions"] == [
        {
            "at": "2026-09-19T10:00:00Z",
            "emoji": "📝",
            "repo": "pvginkel/Example",
            "session": "s-1",
            "client": "mcp",
            "text": DUPLICATE,
        }
    ]
    assert Document(remote.file(observation_path(id_))).observation.model_dump(
        mode="json"
    ) == (observation)


def test_a_duplicate_post_returns_candidates_and_creates_nothing(
    start, auth, remote, clock
):
    with start() as api:
        first = post(api, auth).json()["id"]
        clock.tick()
        api.post(
            f"/api/observations/{first}/reactions",
            json={"emoji": "👍", "repo": "r/b"},
            headers=auth(),
        )

        response = post(api, auth, text="uv sync installs no workspace members at all")

    assert response.status_code == 200
    [candidate] = response.json()["candidates"]
    assert response.json()["id"] is None
    assert candidate["id"] == first
    assert candidate["canonical"] == DUPLICATE
    assert candidate["status"] == "open"
    assert candidate["reactions"] == ["📝 (1)", "👍 (1)"]
    assert candidate["match_class"] == "likely"
    assert 0.6 <= candidate["score"] <= 1
    assert candidate["next_step"].startswith(f'react(id="{first}"')
    assert len(remote.files()) == 1


def test_a_forced_post_creates_despite_candidates(start, auth, remote):
    with start() as api:
        first = post(api, auth).json()["id"]
        response = post(api, auth, force=True)
    forced = response.json()["id"]
    assert response.status_code == 201
    assert len(remote.files()) == 2
    # Slice 003, R3: the force is recorded in the commit message, and the file gains no field.
    assert remote.log() == [
        f"post {forced} (mcp, forced): uv",
        f"post {first} (mcp): uv",
    ]
    assert "forced" not in remote.file(observation_path(forced))


def test_a_forced_post_is_marked_with_nothing_to_match(start, auth, remote):
    """A forced post skips matching, so it is marked whether or not anything would match."""
    with start() as api:
        id_ = post(api, auth, force=True).json()["id"]
    assert remote.log() == [f"post {id_} (mcp, forced): uv"]


def test_a_closed_observation_still_matches(start, auth, remote, pull):
    # FR-3: a skill closed it; the next post still finds it, closed, with its outcome.
    with start() as api:
        id_ = post(api, auth).json()["id"]
        path = observation_path(id_)
        closed = Document(remote.file(path)).with_fields(
            status="closed", outcome="done", pointer="pvginkel/Example#9"
        )
        remote.push({path: closed.text})
        pull(api)

        response = post(api, auth)

    [candidate] = response.json()["candidates"]
    assert (candidate["status"], candidate["outcome"], candidate["pointer"]) == (
        "closed",
        "done",
        "pvginkel/Example#9",
    )
    assert candidate["reason"] is None


def test_a_ruling_reaches_the_next_reporter(start, auth, remote, pull):
    # FR-2, FR-19: the actioner wrote the operator's `no` into `reason`; the next agent to post the
    # same thing is told why, which is the whole delivery for what was ruled and never carded.
    reason = "Expected behaviour: pass --all-packages, as the setup verb does."
    with start() as api:
        id_ = post(api, auth).json()["id"]
        path = observation_path(id_)
        ruled = Document(remote.file(path)).with_fields(
            status="closed", outcome="wont-do", reason=reason
        )
        remote.push({path: ruled.text})
        pull(api)

        response = post(api, auth)

    [candidate] = response.json()["candidates"]
    assert (candidate["id"], candidate["outcome"], candidate["reason"]) == (
        id_,
        "wont-do",
        reason,
    )


def test_a_reaction_is_appended_with_its_provenance(start, auth, remote, clock):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        clock.tick()
        response = api.post(
            f"/api/observations/{id_}/reactions",
            json={
                "emoji": "👎",
                "text": "Fixed upstream.",
                "repo": "pvginkel/Other",
                "session": "s-2",
            },
            headers=auth("skills"),
        )
        observation = api.get(f"/api/observations/{id_}", headers=auth()).json()

    assert response.json() == {"id": id_, "reactions": ["📝 (1)", "👎 (1)"]}
    assert observation["reactions"][-1] == {
        "at": "2026-09-19T10:01:00Z",
        "emoji": "👎",
        "repo": "pvginkel/Other",
        "session": "s-2",
        "client": "skills",
        "text": "Fixed upstream.",
    }
    assert (
        observation["last_seen"]
        == observation["last_updated"]
        == "2026-09-19T10:01:00Z"
    )
    assert observation["created"] == "2026-09-19T10:00:00Z"
    assert observation["repos"] == ["pvginkel/Example", "pvginkel/Other"]
    assert remote.log()[0] == f"react {id_} 👎 (skills)"


def test_a_reaction_leaves_a_hand_edit_alone(start, auth, remote, clock):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        path = observation_path(id_)
        edited = remote.file(path).replace(
            "status: open", "# reviewed\nstatus: proposed"
        )
        remote.push({path: edited})
        clock.tick()

        api.post(
            f"/api/observations/{id_}/reactions",
            json={"emoji": "👍", "repo": "pvginkel/Example"},
            headers=auth(),
        )

    after = remote.file(path)
    assert "# reviewed\nstatus: proposed\n" in after
    assert after.replace("2026-09-19T10:01:00Z", "2026-09-19T10:00:00Z").startswith(
        edited.split("### comments")[0].rstrip()
    )


def test_a_reaction_to_a_closed_observation_is_taken(start, auth, remote):
    # FR-4: allowed; the reconciler reads it as a re-raise.
    with start() as api:
        id_ = post(api, auth).json()["id"]
        path = observation_path(id_)
        remote.push(
            {path: Document(remote.file(path)).with_fields(status="closed").text}
        )
        response = api.post(
            f"/api/observations/{id_}/reactions",
            json={"emoji": "👍", "repo": "x/y"},
            headers=auth(),
        )
    assert response.status_code == 200
    assert "status: closed" in remote.file(path)


def test_a_reaction_to_a_merged_away_observation_is_not_found(start, auth, remote):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        remote.push({observation_path(id_): None}, "merge it away")
        response = api.post(
            f"/api/observations/{id_}/reactions",
            json={"emoji": "👍", "repo": "x/y"},
            headers=auth(),
        )
    assert response.status_code == 404
    assert response.json()["type"] == "not-found"
    assert remote.log()[0] == "merge it away"


def test_a_reaction_to_a_broken_file_is_a_conflict(start, auth, remote):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        path = observation_path(id_)
        remote.push(
            {path: remote.file(path).replace("category: hint", "category: bug")}
        )
        response = api.post(
            f"/api/observations/{id_}/reactions",
            json={"emoji": "👍", "repo": "x/y"},
            headers=auth(),
        )
    assert response.status_code == 409
    assert response.json()["type"] == "conflict"
    assert "category" in response.json()["detail"]


@pytest.mark.parametrize("id_", ["01K5H8ZQ3V6D9W2X4Y7B1C0001", "not-a-ulid"])
def test_an_unknown_or_malformed_id(start, auth, id_):
    with start() as api:
        response = api.get(f"/api/observations/{id_}", headers=auth())
    assert response.status_code == (404 if id_.startswith("01") else 422)


def test_an_invalid_post_is_refused_with_the_fields_at_fault(start, auth):
    with start() as api:
        response = post(api, auth, category="bug", text="  ")
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == "validation-error"
    assert {tuple(error["loc"]) for error in problem["errors"]} == {
        ("body", "category"),
        ("body", "text"),
    }
    assert "input" not in problem["errors"][0]


def test_match_writes_nothing_and_joins_the_area(start, auth, remote, models):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        response = api.post(
            "/api/match", json={"text": DUPLICATE, "area": "uv"}, headers=auth("skills")
        )

    [candidate] = response.json()["candidates"]
    assert candidate["id"] == id_
    assert (candidate["cosine"], candidate["score"]) == (1.0, 1.0)
    assert models.embedded[-1] == f"uv: {DUPLICATE}"
    assert len(remote.log()) == 1


def test_match_names_the_commit_it_scored_until_the_pull_takes_a_push_in(
    start, auth, remote, pull
):
    # The reconciler's `recall` compares it with its own HEAD: a push the API has not pulled yet
    # was not scored, and the reply says so.
    with start() as api:
        post(api, auth)
        posted = remote.head()
        before = indexed_commit(api, auth)
        pushed = remote.push({"observations/README.md": "a skill's push\n"})
        unpulled = indexed_commit(api, auth)
        pull(api)
        pulled = indexed_commit(api, auth)

    assert (before, unpulled, pulled) == (posted, posted, pushed)


def test_match_names_the_commit_the_index_took_in_while_it_lags(
    start, auth, remote, models, pull
):
    """A pull the observation index could not follow leaves `indexed_commit` at the commit
    before it, though the checkout and the triage index moved on, so the reconciler's stale check
    reports stale; the next pull once the models pod answers moves it on."""
    with start() as api:
        id_ = post(api, auth).json()["id"]
        posted = remote.head()
        pushed = rewrite(remote, id_)
        models.fail = True
        pull(api)
        models.fail = False
        lagging = indexed_commit(api, auth)
        pull(api)
        caught_up = indexed_commit(api, auth)

    assert (lagging, caught_up) == (posted, pushed)


def test_neighbors_leave_the_observation_itself_out(start, auth):
    with start() as api:
        first = post(api, auth).json()["id"]
        second = post(
            api, auth, text="grafana dashboards show the browser timezone"
        ).json()["id"]
        response = api.get(
            f"/api/observations/{first}/neighbors?k=3", headers=auth("skills")
        )

    assert response.json()["id"] == first
    assert [n["id"] for n in response.json()["neighbors"]] == [second]
    assert response.json()["neighbors"][0]["match_class"] is None


def test_an_unreachable_models_pod_is_a_502_and_writes_nothing(
    start, auth, remote, models
):
    """FR-9: matching is not a write, and a post without `force` matches first."""
    with start() as api:
        post(api, auth, text="grafana dashboards show the browser timezone")
        before = remote.log()
        models.fail = True
        response = post(api, auth)
    assert response.status_code == 502
    assert response.json()["type"] == "models-unreachable"
    assert remote.log() == before


def test_a_post_with_nothing_to_match_lands_while_the_models_pod_is_down(
    start, auth, remote, models, pull
):
    """FR-9: an empty store needs no embedding to match, and the write lands though the index
    cannot take the new observation in; the next pull once the models pod answers does."""
    with start() as api:
        models.fail = True
        response = post(api, auth)
        id_ = response.json()["id"]
        lagging = api.get(f"/api/observations/{id_}", headers=auth()).status_code
        models.fail = False
        pull(api)
        caught_up = api.get(f"/api/observations/{id_}", headers=auth()).status_code

    assert response.status_code == 201, response.text
    assert remote.log() == [f"post {id_} (mcp): uv"]
    assert (lagging, caught_up) == (404, 200)


def test_a_reaction_lands_while_the_index_cannot_follow_the_tip(
    start, auth, remote, models
):
    """FR-4 (FN-17): the models pod is down after a push that rewrote the observation; the
    reaction lands on the rewritten file and answers success."""
    with start() as api:
        id_ = post(api, auth).json()["id"]
        rewrite(remote, id_)
        models.fail = True
        response = api.post(
            f"/api/observations/{id_}/reactions",
            json={"emoji": "👎", "text": "Still there.", "repo": "pvginkel/Other"},
            headers=auth("skills"),
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"id": id_, "reactions": ["📝 (1)", "👎 (1)"]}
    assert remote.log()[0] == f"react {id_} 👎 (skills)"
    stored = Document(remote.file(observation_path(id_))).observation
    assert stored.canonical == REWRITTEN


def test_a_refused_push_is_a_502_and_writes_nothing(start, auth, remote):
    with start() as api:
        hook = remote.path / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        response = post(api, auth)
    assert response.status_code == 502
    assert response.json()["type"] == "store-unreachable"
    assert remote.log() == []
