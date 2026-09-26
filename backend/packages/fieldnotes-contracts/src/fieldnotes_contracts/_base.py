"""Shared pydantic base for the Fieldnotes wire models.

The wire is snake_case, like the observation files' frontmatter (FR-8) and the MCP tools'
parameters (FR-1, FR-4), so a field reads the same on disk, over REST and in a tool call. Input is
strict: an unknown field is refused rather than dropped.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WireModel(BaseModel):
    """Base for wire models: strict on input, frozen once built."""

    model_config = ConfigDict(extra="forbid", frozen=True)
