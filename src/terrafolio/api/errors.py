"""One error envelope, and the mapping from the store's exceptions onto it.

``docs/api.md`` §1.7 gives every error one shape::

    { "error": { "code": "…", "message": "…", "detail": { } } }

``code`` is a stable machine token, ``message`` is one sentence fit to show a
user, and ``detail`` carries whatever the caller needs to act on it. The two
codes that carry product meaning rather than plumbing — ``PIPELINE_MOVED`` and
``LOCKS_EXCEED_CAPITAL`` — are both §13 cases, and both put enough in ``detail``
for the UI to say which locks to release or which hash it is now holding.

FastAPI's own ``HTTPException`` serialises as ``{"detail": …}``, which is a
second error shape. Raising :class:`ApiError` instead keeps there being one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from terrafolio.store.errors import (
    RunNotFinishedError,
    RunNotFoundError,
    SnapshotConflictError,
    StoreError,
)

__all__ = ["ApiError", "ErrorCode", "error_body", "install_error_handlers"]


class ErrorCode(StrEnum):
    """``docs/api.md`` §11's codes, plus the two the store raises underneath."""

    INVALID_MANDATE = "INVALID_MANDATE"
    INVALID_REQUEST = "INVALID_REQUEST"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    PAGE_NOT_FOUND = "PAGE_NOT_FOUND"
    PIPELINE_MOVED = "PIPELINE_MOVED"
    RUN_EXPIRED = "RUN_EXPIRED"
    """Reserved. Nothing prunes ``run_event``, so no stream expires (§7)."""
    LOCKS_EXCEED_CAPITAL = "LOCKS_EXCEED_CAPITAL"
    NO_CANDIDATES = "NO_CANDIDATES"
    RUN_NOT_FINISHED = "RUN_NOT_FINISHED"
    ENGINE_ERROR = "ENGINE_ERROR"
    STORE_ERROR = "STORE_ERROR"
    PACK_UNAVAILABLE = "PACK_UNAVAILABLE"


class ApiError(Exception):
    """An error the client should see, with the status it should see it at."""

    def __init__(
        self,
        status: HTTPStatus,
        code: ErrorCode,
        message: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail: Mapping[str, Any] = detail if detail is not None else {}


def error_body(code: ErrorCode, message: str, detail: Mapping[str, Any]) -> dict[str, Any]:
    """§1.7's envelope, built in one place so every raiser produces one shape."""
    return {"error": {"code": code.value, "message": message, "detail": dict(detail)}}


MANDATE_LOCATION: Final = ("body", "mandate")
"""Where a mandate sits in a request body, for both endpoints that take one."""


def _mandate_field(location: Sequence[str | int]) -> str | None:
    """The field named in a validation error's location, if it names one.

    ``("body", "mandate", "holdYears")`` names ``holdYears``; a whole-model check
    such as the COD window's reports ``("body", "mandate")`` and names nothing,
    because the problem is the relationship between two fields rather than
    either one of them.
    """
    tail = location[len(MANDATE_LOCATION) :]
    return str(tail[0]) if tail and isinstance(tail[0], str) else None


def _from_validation(error: RequestValidationError) -> ApiError:
    problems = error.errors()
    mandate = [
        problem
        for problem in problems
        if tuple(problem["loc"][: len(MANDATE_LOCATION)]) == MANDATE_LOCATION
    ]
    if not mandate:
        return ApiError(
            HTTPStatus.BAD_REQUEST,
            ErrorCode.INVALID_REQUEST,
            "The request body is not in the expected shape.",
            {"problems": jsonable_encoder(problems)},
        )
    first = mandate[0]
    field = _mandate_field(first["loc"])
    named = f"{field}: " if field else ""
    return ApiError(
        HTTPStatus.BAD_REQUEST,
        ErrorCode.INVALID_MANDATE,
        f"{named}{first['msg']}",
        {"field": field, "problems": jsonable_encoder(mandate)},
    )


def _response(error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=int(error.status),
        content=error_body(error.code, error.message, error.detail),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Route every exception that can reach the boundary through one envelope."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, error: Exception) -> JSONResponse:
        if not isinstance(error, ApiError):  # pragma: no cover - registered by type
            raise error
        return _response(error)

    @app.exception_handler(RunNotFoundError)
    async def _run_not_found(_: Request, error: Exception) -> JSONResponse:
        return _response(
            ApiError(
                HTTPStatus.NOT_FOUND,
                ErrorCode.RUN_NOT_FOUND,
                "No run with that identifier.",
                {"identifier": str(getattr(error, "identifier", ""))},
            )
        )

    @app.exception_handler(RunNotFinishedError)
    async def _run_not_finished(_: Request, error: Exception) -> JSONResponse:
        # 409 rather than 404: the run exists and this will succeed later, which
        # is a different instruction to the caller than "no such run".
        return _response(
            ApiError(
                HTTPStatus.CONFLICT,
                ErrorCode.RUN_NOT_FINISHED,
                "That run has not produced a portfolio yet.",
                {"runId": str(getattr(error, "run_id", ""))},
            )
        )

    @app.exception_handler(SnapshotConflictError)
    async def _snapshot_conflict(_: Request, error: Exception) -> JSONResponse:
        return _response(
            ApiError(
                HTTPStatus.CONFLICT,
                ErrorCode.PIPELINE_MOVED,
                "The pipeline snapshot on record disagrees with the one just loaded.",
                {"pipelineHash": str(getattr(error, "pipeline_hash", ""))},
            )
        )

    @app.exception_handler(StoreError)
    async def _store_error(_: Request, error: Exception) -> JSONResponse:
        return _response(
            ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, ErrorCode.STORE_ERROR, str(error))
        )

    @app.exception_handler(RequestValidationError)
    async def _malformed(request: Request, error: Exception) -> JSONResponse:
        """A body that failed validation, in §1.7's shape.

        A problem inside ``mandate`` is reported as **INVALID_MANDATE** with the
        field named, because §6.1 requires ``detail.field`` and because "your
        hold period is out of range" is a different conversation with the user
        than "this body is not JSON". Anything else is ``INVALID_REQUEST``.
        """
        del request
        if not isinstance(error, RequestValidationError):  # pragma: no cover - by type
            raise error
        return _response(_from_validation(error))
