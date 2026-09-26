"""API blueprints."""

from flask import Blueprint

# Create main API blueprint
api_bp = Blueprint("api", __name__, url_prefix="/api")

# Register OIDC authentication hooks (before_request, after_request, auth_bp)
from app.api.oidc_hooks import register_oidc_hooks  # noqa: E402

register_oidc_hooks(api_bp)

# Register task management sub-blueprint (under /api/tasks)
from app.api.tasks import tasks_bp  # noqa: E402

api_bp.register_blueprint(tasks_bp)

# App-specific blueprints are registered in app/startup.py:register_blueprints()
