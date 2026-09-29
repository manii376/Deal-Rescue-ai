"""One error envelope for every failure:

    {"error": {"code": "<machine_code>", "message": "<human text>", "details": [...] | null}}

Validation details never echo submitted input (it may contain customer notes).
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("deal_rescue.api")


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, details: list[dict[str, Any]] | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(AppError):
    """Business-level validation that cannot be expressed in the request schema."""

    status_code = 422
    code = "validation_error"


class InvalidReferenceError(AppError):
    """A body field points at a record that does not exist *for this customer*.

    The message is identical whether the record is missing or belongs to another
    customer, so responses never reveal other customers' ids.
    """

    status_code = 422
    code = "invalid_reference"

    def __init__(self, field: str, message: str | None = None):
        super().__init__(message or f"{field} does not refer to a record of this customer",
                         details=[{"field": field}])


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def not_found(entity: str) -> NotFoundError:
    return NotFoundError(f"{entity} not found")


_HTTP_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 415: "unsupported_media_type"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content=error_body("validation_error", "Request validation failed", details))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        return JSONResponse(status_code=exc.status_code, content=error_body(code, str(exc.detail)),
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        # Normally prevented by explicit checks; reaching here means a race or a missed check.
        logger.warning("Integrity error: %s", type(exc.orig).__name__)
        return JSONResponse(status_code=409, content=error_body(
            "integrity_error", "The change conflicts with existing records"))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error (%s)", type(exc).__name__)
        return JSONResponse(status_code=500, content=error_body("internal_error", "Internal server error"))
