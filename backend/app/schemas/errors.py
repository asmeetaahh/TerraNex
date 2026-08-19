"""Error envelope schemas.

These models are **documentation only**. No error response is ever serialized through
them: every failure body is built by `app.core.errors.error_response()` as a literal
dict. Their sole job is to appear in the OpenAPI document so the frontend can generate
a typed `ApiError` instead of hand-writing one.

That separation is why `details` can be described precisely below without any risk to
the runtime envelope — changing these annotations cannot change a single response byte.
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorField(BaseModel):
    """One field-level validation failure. Present in `details.fields` on a 422."""

    field: str = Field(description="Dotted path to the offending field.", examples=["latitude"])
    message: str = Field(
        description="What is wrong with it.",
        examples=["Input should be less than or equal to 90"],
    )
    type: str = Field(
        description="Machine-readable validation failure type.",
        examples=["less_than_equal"],
    )


class ValidationErrorDetails(BaseModel):
    """The shape of `details` when `code` is `VALIDATION_ERROR`.

    Documented as its own model so `ErrorField` is emitted into the OpenAPI components
    and the frontend gets a real type for form-error rendering.
    """

    fields: list[ErrorField] = Field(
        description="Every field that failed validation, one entry per failure."
    )


class ErrorDetail(BaseModel):
    code: str = Field(
        description="Stable machine-readable error code. Branch on this, never on `message`.",
        examples=["FARM_NOT_FOUND"],
    )
    message: str = Field(
        description="Human-readable explanation. May change without notice.",
        examples=["Farm 9f8e… does not exist or is not accessible."],
    )
    details: ValidationErrorDetails | dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured context, keyed by error code. When `code` is `VALIDATION_ERROR` "
            "this is `ValidationErrorDetails` (a `fields` array); otherwise it is a "
            'code-specific object, e.g. `{"farm_id": "…"}` for `FARM_NOT_FOUND`, or '
            "empty when there is no extra context."
        ),
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
