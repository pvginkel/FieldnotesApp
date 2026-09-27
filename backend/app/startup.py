"""App-specific startup hooks.

Hook points called by create_app():
  - create_container()
  - register_blueprints()
  - register_error_handlers()
  - register_root_blueprints()

Hook points called by CLI command handlers:
  - register_cli_commands()
  - post_migration_hook()
  - load_test_data_hook()
"""

from __future__ import annotations

import click
from flask import Blueprint, Flask

from app.services.container import ServiceContainer


def create_container() -> ServiceContainer:
    """Create and configure the application's service container."""
    return ServiceContainer()


def register_blueprints(api_bp: Blueprint, app: Flask) -> None:
    """Register all app-specific blueprints on api_bp (under /api prefix)."""
    # Flask refuses a child blueprint once api_bp is registered on an app, and api_bp is a
    # module-level singleton the suites register on an app per test: register the children once.
    if not api_bp._got_registered_once:
        from app.api.fieldnotes import fieldnotes_bp
        from app.api.triage import triage_bp

        api_bp.register_blueprint(fieldnotes_bp)
        api_bp.register_blueprint(triage_bp)


def register_error_handlers(app: Flask) -> None:
    """Register app-specific error handlers."""
    pass


def register_root_blueprints(app: Flask) -> None:
    """Register app-specific blueprints directly on the app (not under /api prefix).

    Use this for internal endpoints, WebSocket handlers, or other routes
    that should not be nested under the /api URL prefix.
    """
    pass


def register_cli_commands(cli: click.Group) -> None:
    """Register app-specific CLI commands."""
    pass


def post_migration_hook(app: Flask) -> None:
    """Run after database migrations (e.g., sync master data)."""
    pass


def load_test_data_hook(app: Flask) -> None:
    """Load test fixtures after database recreation."""
    pass
