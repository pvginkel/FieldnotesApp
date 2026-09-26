"""
Spectree configuration with Pydantic v2 compatibility.
"""
import logging
import re
from typing import Any

from flask import Flask
from spectree import SecurityScheme, SecuritySchemeData, SpecTree
from spectree.models import SecureType

from app.consts import API_DESCRIPTION, API_TITLE

logger = logging.getLogger(__name__)

# Global Spectree instance that can be imported by API modules.
# This will be initialized by configure_spectree() before any imports of the API modules.
# The type is being ignored to not over complicate the code and
# make the type checker happy.
api: SpecTree = None  # type: ignore

# Security scheme name used across the OpenAPI spec
BEARER_AUTH_SCHEME_NAME = "BearerAuth"

# Regex to convert Flask path params to OpenAPI format:
# <int:id>, <string:key>, <key> -> {id}, {key}, {key}
_FLASK_PARAM_RE = re.compile(r"<(?:\w+:)?(\w+)>")


def configure_spectree(app: Flask) -> SpecTree:
    """
    Configure Spectree with proper Pydantic v2 integration and custom settings.

    Defines a BearerAuth security scheme so that per-endpoint security
    annotations can reference it.

    Returns:
        SpecTree: Configured Spectree instance
    """
    global api

    # Define a bearer JWT security scheme for the OpenAPI spec
    bearer_scheme = SecurityScheme(
        name=BEARER_AUTH_SCHEME_NAME,
        data=SecuritySchemeData(  # type: ignore[call-arg]
            type=SecureType.HTTP,
            scheme="bearer",
            bearerFormat="JWT",
        ),
    )

    # Create Spectree instance with Flask backend
    api = SpecTree(
        backend_name="flask",
        title=API_TITLE,
        version="1.0.0",
        description=API_DESCRIPTION,
        path="api/docs",  # OpenAPI docs available at /api/docs
        validation_error_status=400,
        security_schemes=[bearer_scheme],
    )

    # Register the SpecTree with the Flask app to create documentation routes
    api.register(app)

    # Add redirect routes for convenience
    from flask import redirect

    @app.route("/api/docs")
    @app.route("/api/docs/")
    def docs_redirect() -> Any:
        return redirect("/api/docs/swagger/", code=302)

    return api


def annotate_openapi_security(app: Any) -> None:
    """Post-process the OpenAPI spec to add per-endpoint security annotations.

    Iterates all registered routes, determines the effective required role
    for each operation, and injects ``security`` and ``x-required-role``
    into the Spectree spec.  Also stores the per-endpoint role map on the
    app as ``app.openapi_role_map`` for programmatic consumption.

    This function is best-effort: if the spec annotation fails it logs a
    warning and continues rather than crashing the app.

    Args:
        app: The Flask application instance (App with container attribute)
    """
    from app.services.auth_service import AuthService

    try:
        with app.app_context():
            auth_service: AuthService = app.container.auth_service()

            # Build a role map: {openapi_path: {method: role_label}}
            # This is independent of Spectree's spec and works regardless
            # of global SpecTree instance state.
            role_map: dict[str, dict[str, str]] = {}

            for rule in app.url_map.iter_rules():
                if not rule.rule.startswith("/api/"):
                    continue

                view_func = app.view_functions.get(rule.endpoint)
                if view_func is None:
                    continue

                if getattr(view_func, "is_public", False):
                    continue

                openapi_path = _FLASK_PARAM_RE.sub(r"{\1}", rule.rule)

                for method in rule.methods:
                    method_lower = method.lower()
                    if method_lower in ("options", "head"):
                        continue

                    required = auth_service.resolve_required_role(method, view_func)
                    if required is None:
                        continue

                    if isinstance(required, set):
                        role_label = ", ".join(sorted(required))
                    else:
                        role_label = required

                    role_map.setdefault(openapi_path, {})[method_lower] = role_label

            # Store the role map on the app for tests and frontend consumption
            app.openapi_role_map = role_map

            # Build the role configuration summary for the spec root
            auth_roles: dict[str, str | None] = {
                "read": auth_service.read_role,
                "write": auth_service.write_role,
                "admin": auth_service.admin_role,
            }
            app.openapi_auth_roles = auth_roles

            # Now inject into the Spectree spec if it's available.  The
            # spec may not be fully populated when multiple create_app()
            # calls occur (test fixtures), so this is best-effort.
            try:
                spec = api.spec
                spec["x-auth-roles"] = auth_roles

                for path, methods in role_map.items():
                    path_item = spec.get("paths", {}).get(path)
                    if path_item is None:
                        continue
                    for method_lower, role_label in methods.items():
                        operation = path_item.get(method_lower)
                        if operation is None:
                            continue
                        operation["security"] = [{BEARER_AUTH_SCHEME_NAME: []}]
                        operation["x-required-role"] = role_label
            except Exception:
                logger.debug(
                    "Could not annotate Spectree spec (may be stale in test)",
                    exc_info=True,
                )

        logger.info("OpenAPI spec annotated with per-endpoint security")

    except Exception:
        logger.warning(
            "Failed to annotate OpenAPI spec with security information",
            exc_info=True,
        )
