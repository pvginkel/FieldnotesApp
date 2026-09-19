"""The REST endpoints (design, "Services"): post (FR-1..FR-3), react (FR-4), get (FR-5), match,
neighbors, the health checks, bearer auth (NFR-4) and the problem+json errors."""

import pytest

from fieldnotes_api.document import Document
from fieldnotes_api.index import observation_path

DUPLICATE = "uv sync installs no workspace members"


def post(api, auth, text=DUPLICATE, area="uv", **fields):
    body = {"area": area, "category": "hint", "text": text, "repo": "pvginkel/Example"}
    return api.post("/observations", json=body | fields, headers=auth())


def test_health_answers_without_a_token(start):
    with start() as api:
        assert api.get("/healthz").json() == {"status": "ok"}
        assert api.get("/readyz").json() == {"status": "ok"}


def test_a_failed_start_turns_health_red(start):
    with start(ready=False, FIELDNOTES_STORE_URL="/nonexistent/remote.git") as api:
        for _ in range(500):
            if api.get("/healthz").status_code == 503:
                break
        assert api.get("/healthz").json()["title"] == "the Fieldnotes API failed to start"
        problem = api.get("/readyz").json()
        assert (problem["status"], problem["type"]) == (503, "not-ready")


@pytest.mark.parametrize(
    "header",
    [None, "Bearer wrong", "Basic bWNwLXRva2Vu", "Bearer", "mcp-token"],
)
def test_a_request_without_a_known_bearer_is_refused(start, header):
    with start() as api:
        headers = {"Authorization": header} if header else {}
        response = api.get(f"/observations/{'0' * 26}", headers=headers)
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "unauthenticated"


def test_a_novel_post_creates_an_observation(start, auth, remote):
    with start() as api:
        response = post(api, auth, session="s-1")
        id_ = response.json()["id"]
        observation = api.get(f"/observations/{id_}", headers=auth()).json()

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
    assert Document(remote.file(observation_path(id_))).observation.model_dump(mode="json") == (
        observation
    )


def test_a_duplicate_post_returns_candidates_and_creates_nothing(start, auth, remote, clock):
    with start() as api:
        first = post(api, auth).json()["id"]
        clock.tick()
        api.post(
            f"/observations/{first}/reactions", json={"emoji": "👍", "repo": "r/b"}, headers=auth()
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
        post(api, auth)
        response = post(api, auth, force=True)
    assert response.status_code == 201
    assert len(remote.files()) == 2


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


def test_a_reaction_is_appended_with_its_provenance(start, auth, remote, clock):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        clock.tick()
        response = api.post(
            f"/observations/{id_}/reactions",
            json={
                "emoji": "👎",
                "text": "Fixed upstream.",
                "repo": "pvginkel/Other",
                "session": "s-2",
            },
            headers=auth("skills"),
        )
        observation = api.get(f"/observations/{id_}", headers=auth()).json()

    assert response.json() == {"id": id_, "reactions": ["📝 (1)", "👎 (1)"]}
    assert observation["reactions"][-1] == {
        "at": "2026-09-19T10:01:00Z",
        "emoji": "👎",
        "repo": "pvginkel/Other",
        "session": "s-2",
        "client": "skills",
        "text": "Fixed upstream.",
    }
    assert observation["last_seen"] == observation["last_updated"] == "2026-09-19T10:01:00Z"
    assert observation["created"] == "2026-09-19T10:00:00Z"
    assert observation["repos"] == ["pvginkel/Example", "pvginkel/Other"]
    assert remote.log()[0] == f"react {id_} 👎 (skills)"


def test_a_reaction_leaves_a_hand_edit_alone(start, auth, remote, clock):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        path = observation_path(id_)
        edited = remote.file(path).replace("status: open", "# reviewed\nstatus: proposed")
        remote.push({path: edited})
        clock.tick()

        api.post(
            f"/observations/{id_}/reactions",
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
        remote.push({path: Document(remote.file(path)).with_fields(status="closed").text})
        response = api.post(
            f"/observations/{id_}/reactions", json={"emoji": "👍", "repo": "x/y"}, headers=auth()
        )
    assert response.status_code == 200
    assert "status: closed" in remote.file(path)


def test_a_reaction_to_a_merged_away_observation_is_not_found(start, auth, remote):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        remote.push({observation_path(id_): None}, "merge it away")
        response = api.post(
            f"/observations/{id_}/reactions", json={"emoji": "👍", "repo": "x/y"}, headers=auth()
        )
    assert response.status_code == 404
    assert response.json()["type"] == "not-found"
    assert remote.log()[0] == "merge it away"


def test_a_reaction_to_a_broken_file_is_a_conflict(start, auth, remote):
    with start() as api:
        id_ = post(api, auth).json()["id"]
        path = observation_path(id_)
        remote.push({path: remote.file(path).replace("category: hint", "category: bug")})
        response = api.post(
            f"/observations/{id_}/reactions", json={"emoji": "👍", "repo": "x/y"}, headers=auth()
        )
    assert response.status_code == 409
    assert response.json()["type"] == "conflict"
    assert "category" in response.json()["detail"]


@pytest.mark.parametrize("id_", ["01K5H8ZQ3V6D9W2X4Y7B1C0001", "not-a-ulid"])
def test_an_unknown_or_malformed_id(start, auth, id_):
    with start() as api:
        response = api.get(f"/observations/{id_}", headers=auth())
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
            "/match", json={"text": DUPLICATE, "area": "uv"}, headers=auth("skills")
        )

    [candidate] = response.json()["candidates"]
    assert candidate["id"] == id_
    assert (candidate["cosine"], candidate["score"]) == (1.0, 1.0)
    assert models.embedded[-1] == f"uv: {DUPLICATE}"
    assert len(remote.log()) == 1


def test_neighbors_leave_the_observation_itself_out(start, auth):
    with start() as api:
        first = post(api, auth).json()["id"]
        second = post(api, auth, text="grafana dashboards show the browser timezone").json()["id"]
        response = api.get(f"/observations/{first}/neighbors?k=3", headers=auth("skills"))

    assert response.json()["id"] == first
    assert [n["id"] for n in response.json()["neighbors"]] == [second]
    assert response.json()["neighbors"][0]["match_class"] is None


def test_an_unreachable_models_pod_is_a_502(start, auth, models):
    with start() as api:
        models.fail = True
        response = post(api, auth)
    assert response.status_code == 502
    assert response.json()["type"] == "models-unreachable"


def test_a_refused_push_is_a_502_and_writes_nothing(start, auth, remote):
    with start() as api:
        hook = remote.path / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        response = post(api, auth)
    assert response.status_code == 502
    assert response.json()["type"] == "store-unreachable"
    assert remote.log() == []
