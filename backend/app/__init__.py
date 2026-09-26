"""Flask application factory."""

import logging
import sys
from typing import Any

from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from app.app import App
from app.app_config import AppSettings
from app.config import Settings


def create_app(settings: "Settings | None" = None, app_settings: "AppSettings | None" = None, skip_background_services: bool = False) -> App:
    """Create and configure Flask application.

    This factory follows a hook-based pattern where app-specific behavior
    is injected through functions in app/startup.py:
    - create_container(): builds the DI container with app-specific providers
    - register_blueprints(): registers domain resource blueprints on /api
    - register_error_handlers(): registers app-specific error handlers
    - register_root_blueprints(): registers blueprints directly on the app (not under /api)
    """
    app = App(__name__)

    # Load configuration
    if settings is None:
        settings = Settings.load()

    # Validate configuration before proceeding
    settings.validate_production_config()

    app.config.from_object(settings.to_flask_config())

    # Initialize SpecTree for OpenAPI docs
    from app.utils.spectree_config import configure_spectree

    configure_spectree(app)

    # --- Hook 1: Create service container ---
    from app.startup import create_container

    # Load app-specific configuration alongside infrastructure settings
    if app_settings is None:
        app_settings = AppSettings.load(flask_env=settings.flask_env)

    container = create_container()
    container.config.override(settings)
    container.app_config.override(app_settings)

    # Wire container to all API modules via package scanning
    container.wire(packages=['app.api'])

    app.container = container

    # Configure CORS
    CORS(app, origins=settings.cors_origins)

    # Initialize correlation ID tracking
    from app.utils import _init_request_id
    _init_request_id(app)

    # Enable stderr logging in testing mode so that request logs and exception
    # tracebacks appear in the process output captured by Playwright.
    if settings.is_testing:
        root_logger = logging.getLogger()
        if not any(
            isinstance(h, logging.StreamHandler) and getattr(h, 'stream', None) is sys.stderr
            for h in root_logger.handlers
        ):
            stderr_handler = logging.StreamHandler(sys.stderr)
            stderr_handler.setFormatter(logging.Formatter('%(name)s %(levelname)s: %(message)s'))
            root_logger.addHandler(stderr_handler)
        root_logger.setLevel(logging.INFO)

    # Set up log capture handler in testing mode
    if settings.is_testing:
        from app.utils.log_capture import LogCaptureHandler
        log_handler = LogCaptureHandler.get_instance()

        # Set lifecycle coordinator for connection_close events
        lifecycle_coordinator = container.lifecycle_coordinator()
        log_handler.set_lifecycle_coordinator(lifecycle_coordinator)

        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)

        app.logger.info("Log capture handler initialized for testing mode")

    # Register error handlers: core + business (template), then app-specific hook
    from app.utils.flask_error_handlers import (
        register_business_error_handlers,
        register_core_error_handlers,
    )

    register_core_error_handlers(app)
    register_business_error_handlers(app)

    # --- Hook 2: App-specific error handlers ---
    from app.startup import register_error_handlers

    register_error_handlers(app)
    health_service = container.health_service()

    # Register SSE Gateway readiness check with HealthService
    import requests as _requests_module

    def _check_sse_gateway_readiness() -> dict[str, Any]:
        try:
            resp = _requests_module.get(
                f"{settings.sse_gateway_url}/readyz", timeout=2.0
            )
            if resp.status_code == 200:
                return {"reachable": True, "ok": True}
            return {"reachable": True, "status_code": resp.status_code, "ok": False}
        except Exception as e:
            return {"reachable": False, "error": str(e), "ok": False}

    health_service.register_readyz("sse_gateway", _check_sse_gateway_readiness)

    # Register main API blueprint (includes auth hooks and auth_bp)
    from app.api import api_bp

    # --- Hook 3: App-specific blueprint registrations ---
    from app.startup import register_blueprints

    register_blueprints(api_bp, app)

    app.register_blueprint(api_bp)

    # Register template blueprints directly on the app (not under /api)
    # These are for internal cluster use only and should not be publicly proxied
    from app.api.health import health_bp
    from app.api.metrics import metrics_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(metrics_bp)

    # --- Hook 4: App-specific root-level blueprints (not under /api) ---
    from app.startup import register_root_blueprints

    register_root_blueprints(app)

    # Always register testing blueprints (runtime check handles access control)
    from app.api.testing_logs import testing_logs_bp
    app.register_blueprint(testing_logs_bp)

    from app.api.testing_sse import testing_sse_bp
    app.register_blueprint(testing_sse_bp)

    # Register SSE Gateway callback blueprint
    from app.api.sse import sse_bp
    app.register_blueprint(sse_bp)

    # Register testing auth endpoints (runtime check handles access control)
    from app.api.testing_auth import testing_auth_bp
    app.register_blueprint(testing_auth_bp)

    # --- Role-based access startup hooks ---
    # Validate @allow_roles decorators against configured roles (fail fast on typos)
    if settings.oidc_enabled:
        from app.utils.auth import validate_allow_roles_at_startup
        validate_allow_roles_at_startup(app, container.auth_service())

    # Annotate OpenAPI spec with per-endpoint security information
    from app.utils.spectree_config import annotate_openapi_security
    annotate_openapi_security(app)

    # Start background services only when not in CLI mode
    if not skip_background_services:
        # Eagerly instantiate and start all registered background services
        from app.services.container import start_background_services

        start_background_services(container)

        # Signal that application startup is complete. Services that registered
        # for STARTUP notifications will be invoked here.
        container.lifecycle_coordinator().fire_startup()

    # Flask documents replacing wsgi_app with middleware, but declares it as a
    # method, so mypy reads the assignment as clobbering one.
    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app,
        x_proto=1,
        x_host=1,
        x_for=1,
    )

    return app
