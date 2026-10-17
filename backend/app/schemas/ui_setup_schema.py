"""Schema for GET /api/ui-setup."""

from typing import Literal

from pydantic import BaseModel, Field


class UiSetupResponseSchema(BaseModel):
    """What the frontend needs to know about the deployment it runs against."""

    stage: Literal["local-dev", "dev", "uat", "prd", "local-tst", "tst"] = Field(
        description="Deployment stage (STAGE)",
        examples=["prd", "local-dev"],
    )
    expose_internals: bool = Field(
        description=(
            "Whether the app may show internal detail, such as exception text and "
            "diagnostics: STAGE is not prd, or the app is marked HOMELAB_INTERNAL"
        ),
    )
