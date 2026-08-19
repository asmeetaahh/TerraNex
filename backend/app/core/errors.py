"""Error taxonomy and the single JSON error envelope used by every failure path.

Contract rule: `code` is the API. `message` is for humans and may change freely.
The frontend must branch on `code`, never on message text.
"""

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    FARM_NOT_FOUND = "FARM_NOT_FOUND"
    CROP_NOT_FOUND = "CROP_NOT_FOUND"
    IMAGE_NOT_FOUND = "IMAGE_NOT_FOUND"
    NO_ANALYSIS_YET = "NO_ANALYSIS_YET"
    CONFLICT = "CONFLICT"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    AI_INVALID_OUTPUT = "AI_INVALID_OUTPUT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """Base for every error we raise deliberately.

    Subclasses set `code` and `http_status`; call sites supply a human message
    and optional structured `details`.
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message = message or self.__class__.__doc__ or "An error occurred."
        self.details = details or {}
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        super().__init__(self.message)


class ValidationError(AppError):
    """The request was malformed or failed validation."""

    code = ErrorCode.VALIDATION_ERROR
    http_status = 422  # constant name churned across Starlette versions


class UnauthorizedError(AppError):
    """Authentication is required and was missing or invalid."""

    code = ErrorCode.UNAUTHORIZED
    http_status = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    """This resource belongs to another user."""

    code = ErrorCode.FORBIDDEN
    http_status = status.HTTP_403_FORBIDDEN


class NotFoundError(AppError):
    """The requested resource does not exist."""

    code = ErrorCode.RESOURCE_NOT_FOUND
    http_status = status.HTTP_404_NOT_FOUND


class FarmNotFoundError(NotFoundError):
    """The farm does not exist or is not accessible."""

    code = ErrorCode.FARM_NOT_FOUND


class NoAnalysisYetError(NotFoundError):
    """No analysis has been run for this farm yet."""

    code = ErrorCode.NO_ANALYSIS_YET


class ConflictError(AppError):
    """The request conflicts with the current state of the resource."""

    code = ErrorCode.CONFLICT
    http_status = status.HTTP_409_CONFLICT


class ImageTooLargeError(AppError):
    """The uploaded image exceeds the size limit."""

    code = ErrorCode.IMAGE_TOO_LARGE
    http_status = 413  # constant name churned across Starlette versions


class UnsupportedMediaTypeError(AppError):
    """The uploaded file type is not supported."""

    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    http_status = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class ProviderError(AppError):
    """An external data provider is unavailable."""

    code = ErrorCode.PROVIDER_UNAVAILABLE
    http_status = status.HTTP_502_BAD_GATEWAY


class AIUnavailableError(AppError):
    """The AI service is unavailable."""

    code = ErrorCode.AI_UNAVAILABLE
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


class AIInvalidOutputError(AppError):
    """The AI returned output that failed schema validation."""

    code = ErrorCode.AI_INVALID_OUTPUT
    http_status = status.HTTP_502_BAD_GATEWAY


class RateLimitedError(AppError):
    """Too many requests."""

    code = ErrorCode.RATE_LIMITED
    http_status = status.HTTP_429_TOO_MANY_REQUESTS


# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------


def error_response(
    *,
    code: str,
    message: str,
    http_status: int,
    request_id: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Render the one and only error shape the API ever returns."""
    return JSONResponse(
        status_code=http_status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            }
        },
        headers={"X-Request-Id": request_id},
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def register_exception_handlers(app: FastAPI) -> None:
    """Wire every failure mode onto the single envelope."""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app_error",
            extra={
                "error_code": exc.code,
                "path": request.url.path,
                "request_id": _request_id(request),
                "details": exc.details,
            },
        )
        return error_response(
            code=exc.code,
            message=exc.message,
            http_status=exc.http_status,
            request_id=_request_id(request),
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default 422 body has a different shape. Override it so the
        # frontend needs exactly one error parser, not two.
        fields = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
                "message": err.get("msg", "invalid"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        return error_response(
            code=ErrorCode.VALIDATION_ERROR,
            message="Request validation failed.",
            http_status=422,
            request_id=_request_id(request),
            details={"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            401: ErrorCode.UNAUTHORIZED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.RESOURCE_NOT_FOUND,
            405: ErrorCode.VALIDATION_ERROR,
            429: ErrorCode.RATE_LIMITED,
        }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return error_response(
            code=code,
            message=str(exc.detail),
            http_status=exc.status_code,
            request_id=_request_id(request),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log the traceback; never leak it to the client. The request_id in the
        # response is enough to find this log line.
        logger.exception(
            "unhandled_exception",
            extra={"path": request.url.path, "request_id": _request_id(request)},
        )
        return error_response(
            code=ErrorCode.INTERNAL_ERROR,
            message="An unexpected error occurred.",
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=_request_id(request),
        )
