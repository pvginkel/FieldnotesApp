"""What the frontend needs to know about the deployment it runs against."""

from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint
from spectree import Response as SpectreeResponse

from app.config import Settings
from app.schemas.ui_setup_schema import UiSetupResponseSchema
from app.services.container import ServiceContainer
from app.utils.spectree_config import api

ui_setup_bp = Blueprint("ui_setup", __name__, url_prefix="/ui-setup")


@ui_setup_bp.route("", methods=["GET"])
@api.validate(resp=SpectreeResponse(HTTP_200=UiSetupResponseSchema))
@inject
def get_ui_setup(config: Settings = Provide[ServiceContainer.config]) -> Any:
    """Return the deployment stage, and whether the UI may show internal detail."""
    return UiSetupResponseSchema(
        stage=config.stage.value,
        expose_internals=config.expose_internals,
    ).model_dump()
