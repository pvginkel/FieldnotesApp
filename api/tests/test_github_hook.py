"""`POST /hooks/github` (FR-9, FR-20; design, "Webhooks"). The "GitHub webhook" validation row is
`test_a_signed_push_...` and `test_a_bad_signature_...`."""

import json

import pytest

from fieldnotes_api.document import Document
from fieldnotes_api.hooks import github_signature
from fieldnotes_api.index import observation_path


@pytest.fixture
def delivery(environ):
    """`delivery(...)`: a signed push delivery's body and headers; the arguments change it."""

    def build(event="push", repo=None, ref="refs/heads/main", secret=None):
        repo = repo or environ["FIELDNOTES_GITHUB_REPO"]
        secret = secret or environ["FIELDNOTES_GITHUB_WEBHOOK_SECRET"]
        return sign(event, repo, ref, secret)

    return build


def sign(event, repo, ref, secret):
    body = json.dumps({"ref": ref, "repository": {"full_name": repo}}).encode()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": github_signature(secret, body),
    }
    return body, headers


@pytest.fixture
def pulls(monkeypatch):
    """Count the pulls the hook queues."""

    def count(api):
        store = api.app.state.runtime.store
        queued = []
        original = store.pull

        def pull():
            queued.append(True)
            return original()

        monkeypatch.setattr(store, "pull", pull)
        return queued

    return count


def post(api, auth, text):
    body = {"area": "uv", "category": "hint", "text": text, "repo": "pvginkel/Example"}
    return api.post("/observations", json=body, headers=auth()).json()["id"]


def test_a_signed_push_pulls_and_reembeds_only_the_edited_observation(
    start, auth, remote, models, delivery, eventually
):
    with start() as api:
        edited = post(api, auth, "grafana dashboards show the browser timezone")
        other = post(api, auth, "uv sync installs no workspace members")
        before = api.get(f"/observations/{other}/neighbors", headers=auth("skills")).json()
        path = observation_path(edited)
        rewritten = Document(remote.file(path)).with_fields(canonical="uv sync installs no members")
        remote.push({path: rewritten.text}, "reconciler: rewrite")
        models.embedded.clear()

        body, headers = delivery()
        response = api.post("/hooks/github", content=body, headers=headers)
        eventually(
            lambda: (
                api.get(f"/observations/{edited}", headers=auth()).json()["canonical"]
                == "uv sync installs no members"
            ),
            "the pull",
        )
        after = api.get(f"/observations/{other}/neighbors", headers=auth("skills")).json()

    assert response.status_code == 200
    assert response.json() == {"action": "queued"}
    assert models.embedded[0] == "uv: uv sync installs no members"
    assert models.embedded[1:] == ["uv: uv sync installs no workspace members"]  # the query
    assert before["neighbors"][0]["match_class"] is None
    assert after["neighbors"][0]["id"] == edited
    assert after["neighbors"][0]["match_class"] == "likely"


def test_a_bad_signature_is_refused_and_pulls_nothing(start, pulls, delivery):
    with start() as api:
        queued = pulls(api)
        body, headers = delivery(secret="not-the-secret")
        response = api.post("/hooks/github", content=body, headers=headers)
        unsigned = api.post("/hooks/github", content=body, headers={"X-GitHub-Event": "push"})

    assert response.status_code == unsigned.status_code == 401
    assert response.json()["type"] == "unauthenticated"
    assert queued == []


def test_a_body_changed_after_signing_is_refused(start, pulls, delivery):
    with start() as api:
        queued = pulls(api)
        body, headers = delivery()
        response = api.post("/hooks/github", content=body + b" ", headers=headers)
    assert response.status_code == 401
    assert queued == []


@pytest.mark.parametrize(
    "kind",
    [
        {"event": "ping"},
        {"event": "issues"},
        {"ref": "refs/heads/feature"},
        {"repo": "pvginkel/SomethingElse"},
    ],
)
def test_everything_but_a_push_to_the_stores_main_is_ignored(start, pulls, delivery, kind):
    with start() as api:
        queued = pulls(api)
        body, headers = delivery(**kind)
        response = api.post("/hooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"action": "ignored"}
    assert queued == []


def test_the_repo_matches_whatever_its_case(start, pulls, delivery, environ):
    with start() as api:
        queued = pulls(api)
        body, headers = delivery(repo=environ["FIELDNOTES_GITHUB_REPO"].upper())
        assert api.post("/hooks/github", content=body, headers=headers).json()["action"] == "queued"
    assert queued == [True]


def test_without_a_secret_every_delivery_is_refused(start, pulls, delivery):
    with start(FIELDNOTES_GITHUB_WEBHOOK_SECRET="", FIELDNOTES_GITHUB_REPO="") as api:
        queued = pulls(api)
        body, headers = delivery()
        assert api.post("/hooks/github", content=body, headers=headers).status_code == 401
    assert queued == []
