"""A request binds the SSE connection it names (NFR-4, FN-38). The gateway's connect binds a page's
connection to the user of the access token it forwards, so a page that connected before its token
was refreshed, or reconnected after a rollout, holds a connection bound to no one, and a task begun
for its user does not reach it. An `/api` request that authenticated as a user and names that
connection in `X-SSE-Request-Id` binds it to the user; a bound connection is never rebound, a
request that authenticated as no one binds nothing, and a name this pod does not hold is ignored.
With OIDC on, as on prd, but where the connect's `local-user` binding is the rule under test; every
token and subject is invented."""

import time
from typing import Any

import pytest
from flask import Flask
from pydantic import BaseModel

from app.schemas.task_schema import TaskStatus
from app.services.base_task import BaseTask, ProgressHandle
from app.services.oidc_client_service import TokenResponse

HEADER = "X-SSE-Request-Id"
EDITOR = "sse-editor"
OTHER = "sse-other"
# A route under /api every signed-in user may read; the task it names does not exist.
READ = "/api/tasks/no-such-task/status"
# A write only an editor may make; a viewer is refused it.
WRITE = "/api/tasks/no-such-task/cancel"


class Done(BaseModel):
    done: bool = True


class Quick(BaseTask):
    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        progress_handle.send_progress_text("working")
        return Done()


def connect(app: Flask, request_id: str, token: str | None = None) -> None:
    """The gateway's connect callback for a page's connection, forwarding its access cookie when the
    page holds one."""
    headers = {} if token is None else {"cookie": f"access_token={token}"}
    reply = app.test_client().post(
        "/api/sse/callback",
        json={
            "action": "connect",
            "token": f"gateway-{request_id}",
            "request": {
                "url": f"http://sse.invalid/api/sse/stream?request_id={request_id}",
                "headers": headers,
            },
        },
    )
    assert reply.status_code == 200, reply.get_data(as_text=True)


def bound(app: Flask, request_id: str) -> str | None:
    info = app.container.sse_connection_manager().get_connection_info(request_id)
    assert info is not None, f"no connection {request_id}"
    return info.subject


def listen(app: Flask, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """The task events each connection hears, by its request id."""
    heard: dict[str, list[str]] = {}
    manager = app.container.sse_connection_manager()

    def sent(token, event_data, event_name, service_type, request_id=None):
        if event_name == "task_event":
            heard.setdefault(token.removeprefix("gateway-"), []).append(
                event_data["event_type"]
            )
        return True

    monkeypatch.setattr(manager, "_send_event_to_token", sent)
    return heard


def run_task(app: Flask, subject: str) -> None:
    """A task begun for `subject`, run to its end."""
    tasks = app.container.task_service()
    task_id = tasks.start_task(Quick(), caller_subject=subject).task_id
    deadline = time.monotonic() + 10
    while tasks.get_task_status(task_id).status != TaskStatus.COMPLETED:
        assert time.monotonic() < deadline, "the task did not end"
        time.sleep(0.01)


EVENTS = ["task_started", "progress_update", "task_completed"]


def test_a_request_binds_the_connection_the_connect_left_bound_to_no_one(
    oidc_app, generate_test_jwt, monkeypatch
):
    heard = listen(oidc_app, monkeypatch)
    connect(oidc_app, "page")
    connect(oidc_app, "bystander")
    client = oidc_app.test_client()
    client.set_cookie(
        "access_token", generate_test_jwt(subject=EDITOR, roles=["editor"])
    )
    assert bound(oidc_app, "page") is None
    run_task(oidc_app, EDITOR)
    assert heard == {}

    reply = client.get(READ, headers={HEADER: "page"})
    run_task(oidc_app, EDITOR)

    assert reply.status_code == 404
    assert bound(oidc_app, "page") == EDITOR
    assert bound(oidc_app, "bystander") is None
    assert heard == {"page": EVENTS}


def test_a_request_that_refreshes_the_login_binds_the_connection(
    oidc_app, generate_test_jwt, monkeypatch
):
    """The page opened after its access token lapsed: the connect forwards the lapsed token and
    binds no one, and the request that refreshes the login binds the connection."""
    lapsed = generate_test_jwt(subject=EDITOR, roles=["editor"], expired=True)
    fresh = generate_test_jwt(subject=EDITOR, roles=["editor"])
    monkeypatch.setattr(
        oidc_app.container.oidc_client_service(),
        "refresh_access_token",
        lambda refresh_token: TokenResponse(
            access_token=fresh,
            id_token=None,
            refresh_token=None,
            token_type="Bearer",
            expires_in=300,
        ),
    )
    heard = listen(oidc_app, monkeypatch)
    connect(oidc_app, "page", lapsed)
    client = oidc_app.test_client()
    client.set_cookie("access_token", lapsed)
    client.set_cookie("refresh_token", "invented-refresh-token")

    reply = client.get(READ, headers={HEADER: "page"})
    run_task(oidc_app, EDITOR)

    assert reply.status_code == 404
    assert "access_token=" in reply.headers["Set-Cookie"]
    assert bound(oidc_app, "page") == EDITOR
    assert heard == {"page": EVENTS}


def test_a_request_refused_for_its_role_binds_the_connection_to_its_user(
    oidc_app, generate_test_jwt
):
    connect(oidc_app, "page")
    client = oidc_app.test_client()
    client.set_cookie(
        "access_token", generate_test_jwt(subject=OTHER, roles=["viewer"])
    )

    reply = client.post(WRITE, headers={HEADER: "page"})

    assert reply.status_code == 403
    assert bound(oidc_app, "page") == OTHER


@pytest.mark.parametrize("by", ["the connect", "a request"])
def test_a_connection_bound_to_a_user_is_never_rebound(
    oidc_app, generate_test_jwt, monkeypatch, by
):
    heard = listen(oidc_app, monkeypatch)
    editor = generate_test_jwt(subject=EDITOR, roles=["editor"])
    client = oidc_app.test_client()
    if by == "the connect":
        connect(oidc_app, "page", editor)
    else:
        connect(oidc_app, "page")
        client.set_cookie("access_token", editor)
        assert client.get(READ, headers={HEADER: "page"}).status_code == 404
    client.set_cookie(
        "access_token", generate_test_jwt(subject=OTHER, roles=["editor"])
    )

    reply = client.get(READ, headers={HEADER: "page"})
    run_task(oidc_app, OTHER)

    assert reply.status_code == 404
    assert bound(oidc_app, "page") == EDITOR
    assert heard == {}


@pytest.mark.parametrize(
    ("token", "headers"),
    [
        (None, {}),
        (None, {"Authorization": "Bearer mcp-token"}),
        ("lapsed", {}),
    ],
    ids=["no session", "an agent's bearer", "a lapsed token and no refresh token"],
)
def test_a_request_that_authenticated_as_no_one_binds_nothing(
    oidc_app, generate_test_jwt, token, headers
):
    connect(oidc_app, "page")
    client = oidc_app.test_client()
    if token == "lapsed":
        client.set_cookie(
            "access_token",
            generate_test_jwt(subject=EDITOR, roles=["editor"], expired=True),
        )

    reply = client.get(READ, headers={HEADER: "page", **headers})

    assert reply.status_code == 401
    assert bound(oidc_app, "page") is None


def test_a_name_this_pod_does_not_hold_is_ignored(oidc_app, generate_test_jwt):
    """A connection the other pod holds, or none: the request is served as any other, and no binding
    waits for a connection that arrives under that name later."""
    client = oidc_app.test_client()
    client.set_cookie(
        "access_token", generate_test_jwt(subject=EDITOR, roles=["editor"])
    )

    reply = client.get(READ, headers={HEADER: "elsewhere"})
    connect(oidc_app, "elsewhere")

    assert reply.status_code == 404
    assert bound(oidc_app, "elsewhere") is None


def test_with_oidc_off_the_connects_local_user_binding_stands(app, monkeypatch):
    """With OIDC off every connection is bound to `local-user` at connect and hears every task; a
    request through a test session, the one way a request authenticates with OIDC off, rebinds
    nothing."""
    heard = listen(app, monkeypatch)
    connect(app, "page")
    client = app.test_client()
    session = app.container.testing_service().create_session(
        subject=EDITOR, roles=["editor"]
    )
    client.set_cookie("access_token", session)

    reply = client.get(READ, headers={HEADER: "page"})
    run_task(app, OTHER)

    assert reply.status_code == 404
    assert bound(app, "page") == "local-user"
    assert heard == {"page": EVENTS}
