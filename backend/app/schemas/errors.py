"""Error envelope schemas.

These exist purely so the error shape appears in the OpenAPI document — which is
what lets the frontend generate a typed `ApiError` instead of hand-writing one.
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorField(BaseModel):
    """One field-level validation failure (present in 422 responses)."""

    field: str = Field(description="Dotted path to the offending field.")
    message: str = Field(description="What is wrong with it.")
    type: str = Field(description="Machine-readable validation failure type.")


class ErrorDetail(BaseModel):
    code: str = Field(
        description="Stable machine-readable error code. Branch on this, never on `message`.",
        examples=["FARM_NOT_FOUND"],
    )
    message: str = Field(
        description="Human-readable explanation. May change without notice.",
        examples=["Farm 9f8e… does not exist or is not accessible."],
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context. For 422 this contains `fields: ErrorField[]`.",
    )
    request_id: str = Field(
        description="Correlation id, also returned in the X-Request-Id header.",
        examples=["req_01hzy8k3m2n4p5q6"],
    )


class ErrorResponse(BaseModel):
    """The one and only error shape returned by every 4xx/5xx response."""

    error: ErrorDetail


# Reusable OpenAPI `responses=` fragments so every route documents its failures
# consistently without repeating the schema.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Bad request"},
    401: {"model": ErrorResponse, "description": "Missing or invalid credentials"},
    403: {"model": ErrorResponse, "description": "Resource belongs to another user"},
    404: {"model": ErrorResponse, "description": "Resource not found"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    500: {"model": ErrorResponse, "description": "Internal error"},
}
