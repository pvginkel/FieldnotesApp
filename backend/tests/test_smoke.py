"""Smoke tests for the generated app: it starts, answers its probes, and gates sign-in on `editor`."""

from typing import Any


def test_liveness_answers(client: Any) -> None:
    # Not readyz: readiness includes the SSE gateway, which a unit test has no process for.
    assert client.get("/health/healthz").status_code == 200


def test_self_admits_an_editor(oidc_client: Any, generate_test_jwt: Any) -> None:
    oidc_client.set_cookie("access_token", generate_test_jwt(roles=["editor"]))

    response = oidc_client.get("/api/auth/self")

    assert response.status_code == 200


def test_self_refuses_a_user_without_editor(oidc_client: Any, generate_test_jwt: Any) -> None:
    oidc_client.set_cookie("access_token", generate_test_jwt(roles=["viewer"]))

    response = oidc_client.get("/api/auth/self")

    assert response.status_code == 403
