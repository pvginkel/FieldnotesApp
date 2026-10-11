"""The access log masks the SSE gateway's callback secret, which rides in the callback's query
string. The line is paste's TransLogger's, as Waitress serves the app in production."""

import io
import logging

import pytest
from flask import Flask
from paste.translogger import TransLogger  # type: ignore[import-untyped]

from app.access_log import ACCESS_LOGGER, RedactSecret

SECRET = "s3cr3t-value"


def test_the_app_installs_the_filter_on_the_access_logger(app: Flask) -> None:
    assert any(
        isinstance(f, RedactSecret) for f in logging.getLogger(ACCESS_LOGGER).filters
    )


@pytest.mark.parametrize(
    "uri",
    [
        f"/api/sse/callback?secret={SECRET}",
        f"/api/sse/callback?event=connect&secret={SECRET}&x=1",
    ],
)
def test_a_served_callback_is_logged_without_its_secret(
    app: Flask, caplog: pytest.LogCaptureFixture, uri: str
) -> None:
    path, _, query = uri.partition("?")
    wsgi = TransLogger(app, setup_console_handler=False)
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "3401",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "REMOTE_ADDR": "127.0.0.1",
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": True,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "wsgi.version": (1, 0),
    }
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        body = wsgi(environ, lambda status, headers, exc_info=None: None)
        b"".join(body)
        getattr(body, "close", lambda: None)()

    lines = [r.getMessage() for r in caplog.records if r.name == ACCESS_LOGGER]
    assert any("/api/sse/callback?" in line for line in lines)
    assert all(SECRET not in line for line in lines)
    assert any("secret=<redacted>" in line for line in lines)


def test_a_line_without_a_secret_is_left_alone() -> None:
    record = logging.LogRecord(
        ACCESS_LOGGER, logging.INFO, "", 0, "GET /api/inbox?x=%s", ("1",), None
    )
    RedactSecret().filter(record)
    assert record.getMessage() == "GET /api/inbox?x=1"
