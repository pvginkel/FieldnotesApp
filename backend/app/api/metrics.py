"""Metrics API for Prometheus scraping endpoint."""

from typing import Any

from flask import Blueprint, Response
from prometheus_client import generate_latest

metrics_bp = Blueprint("metrics", __name__, url_prefix="/metrics")


@metrics_bp.route("", methods=["GET"])
def get_metrics() -> Any:
    """Return metrics in Prometheus text format.

    Returns:
        Response with metrics data in Prometheus exposition format
    """
    metrics_text = generate_latest().decode("utf-8")

    return Response(
        metrics_text,
        content_type='text/plain; version=0.0.4; charset=utf-8'
    )
