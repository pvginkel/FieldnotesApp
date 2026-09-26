"""Health check endpoints for Kubernetes probes."""

from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, jsonify, request
from spectree import Response as SpectreeResponse

from app.schemas.health_schema import HealthResponse
from app.services.container import ServiceContainer
from app.services.health_service import HealthService
from app.utils.spectree_config import api

health_bp = Blueprint("health", __name__, url_prefix="/health")


@health_bp.route("/readyz", methods=["GET"])
@api.validate(resp=SpectreeResponse(HTTP_200=HealthResponse, HTTP_503=HealthResponse))
@inject
def readyz(
    health_service: HealthService = Provide[ServiceContainer.health_service],
) -> Any:
    """Readiness probe endpoint for Kubernetes.

    Returns 503 when the application is shutting down, or any registered
    readiness check fails. This signals Kubernetes to remove the pod
    from service endpoints.
    """
    result, status = health_service.check_readyz()
    return jsonify(result), status


@health_bp.route("/healthz", methods=["GET"])
@api.validate(resp=SpectreeResponse(HTTP_200=HealthResponse))
@inject
def healthz(
    health_service: HealthService = Provide[ServiceContainer.health_service],
) -> Any:
    """Liveness probe endpoint for Kubernetes.

    Always returns 200 to indicate the application is alive.
    This keeps the pod running even during graceful shutdown.
    """
    result, status = health_service.check_healthz()
    return jsonify(result), status


@health_bp.route("/drain", methods=["GET"])
@api.validate(resp=SpectreeResponse(HTTP_200=HealthResponse, HTTP_401=HealthResponse))
@inject
def drain(
    health_service: HealthService = Provide[ServiceContainer.health_service],
) -> Any:
    """Drain endpoint for manual graceful shutdown initiation.

    Requires bearer token authentication against DRAIN_AUTH_KEY config setting.
    Calls shutdown() on the lifecycle coordinator and returns health status.
    """
    auth_header = request.headers.get("Authorization", "")
    result, status = health_service.drain(auth_header)
    return jsonify(result), status
