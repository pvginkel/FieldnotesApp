"""HTTP request metrics for every route, independent of DIAGNOSTICS_ENABLED.

- ``http_request_total{method,endpoint,status}``: completed requests.
- ``http_request_duration_seconds{method,endpoint,status}``: request duration.

``endpoint`` is the Flask endpoint name, or ``unknown`` for a request that
matched no route, so scans of random paths cannot grow the label set.
"""

import time

from flask import Flask, g, request
from flask.wrappers import Response
from prometheus_client import Counter, Histogram

HTTP_REQUEST_TOTAL = Counter(
    "http_request_total",
    "Total HTTP requests completed",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint", "status"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def register_http_metrics(app: Flask) -> None:
    """Record every request's count and duration through before/after_request hooks."""

    @app.before_request
    def _start_http_timer() -> None:
        g._http_metrics_start = time.perf_counter()

    @app.after_request
    def _record_http_metrics(response: Response) -> Response:
        start = getattr(g, "_http_metrics_start", None)
        if start is None:
            return response

        labels = {
            "method": request.method,
            "endpoint": request.endpoint or "unknown",
            "status": str(response.status_code),
        }
        HTTP_REQUEST_TOTAL.labels(**labels).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(**labels).observe(time.perf_counter() - start)

        return response
