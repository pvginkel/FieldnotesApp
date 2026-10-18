"""Common response schemas for consistent API structure."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SortDirection = Literal["asc", "desc"]


class ErrorDetailsSchema(BaseModel):
    """Error details object."""

    model_config = ConfigDict(from_attributes=True)

    message: str = Field(..., description="Additional error context")
    field: str | None = Field(None, description="Field name for validation errors")


class ErrorResponseSchema(BaseModel):
    """Standard error response format."""

    model_config = ConfigDict(from_attributes=True)

    error: str = Field(
        ...,
        description="Error message",
        json_schema_extra={"example": "Validation failed"},
    )
    details: ErrorDetailsSchema | list[ErrorDetailsSchema] = Field(
        ..., description="Additional error details"
    )


class ApiErrorDetailsSchema(BaseModel):
    """The ``details`` object of the error envelope ``build_error_response`` builds."""

    model_config = ConfigDict(from_attributes=True)

    message: str = Field(..., description="Human-readable error description")


class ApiErrorResponseSchema(BaseModel):
    """The error envelope every error handler answers with.

    It mirrors ``build_error_response()`` in ``app/utils/flask_error_handlers.py``;
    change both together. Use it to document error responses in ``@api.validate``,
    for example ``HTTP_502=ApiErrorResponseSchema`` on an endpoint that calls an
    external service. One handler differs: a ``pydantic.ValidationError`` raised
    in a handler answers 400 with ``details.errors``, a list of
    ``{"message", "field"}``, instead of ``details.message``.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    error: str = Field(
        ...,
        description="Error message",
        json_schema_extra={"example": "Authorization failed"},
    )
    details: ApiErrorDetailsSchema = Field(..., description="Error details")
    code: str | None = Field(
        None,
        description="Machine-readable error code; every business-logic exception sets one",
        json_schema_extra={"example": "AUTHORIZATION_FAILED"},
    )
    correlation_id: str | None = Field(
        None,
        alias="correlationId",
        description="The request's correlation ID, when it has one",
    )


class SuccessResponseSchema(BaseModel):
    """Standard success response format."""

    model_config = ConfigDict(from_attributes=True)

    message: str = Field(
        ...,
        description="Success message",
        json_schema_extra={"example": "Operation completed successfully"},
    )
    data: Any | None = Field(None, description="Response data")


class MessageResponseSchema(BaseModel):
    """Simple message response format."""

    model_config = ConfigDict(from_attributes=True)

    message: str = Field(
        ...,
        description="Response message",
        json_schema_extra={"example": "Record deleted successfully"},
    )


class PaginationMetaSchema(BaseModel):
    """Pagination metadata."""

    model_config = ConfigDict(from_attributes=True)

    page: int = Field(
        ..., description="Current page number", json_schema_extra={"example": 1}
    )
    per_page: int = Field(
        ..., description="Items per page", json_schema_extra={"example": 20}
    )
    total: int = Field(
        ..., description="Total number of items", json_schema_extra={"example": 150}
    )
    total_pages: int = Field(
        ..., description="Total number of pages", json_schema_extra={"example": 8}
    )
    sort_by: str | None = Field(None, description="The column the list is sorted by")
    sort_dir: SortDirection | None = Field(None, description="Sort direction")


class PaginationQuerySchema(BaseModel):
    """Base query schema for a paged list endpoint.

    Subclass it and add ``sort_by`` as a ``Literal`` of the columns the endpoint
    sorts by, with a default; pass both to ``app.utils.pagination.apply_sorting()``,
    which adds the ``id`` tiebreaker that keeps pages stable.
    """

    model_config = ConfigDict(from_attributes=True)

    page: int = Field(1, ge=1, description="Page number, from 1")
    per_page: int = Field(20, ge=1, le=100, description="Items per page")
    sort_dir: SortDirection = Field("asc", description="Sort direction")

    @property
    def offset(self) -> int:
        """The number of rows before this page."""
        return (self.page - 1) * self.per_page


class PaginatedResponseSchema(BaseModel):
    """Paginated response format."""

    model_config = ConfigDict(from_attributes=True)

    data: list[Any] = Field(..., description="Response data items")
    meta: PaginationMetaSchema = Field(..., description="Pagination metadata")
