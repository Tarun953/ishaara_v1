"""Application errors and their RFC 9457 problem+json rendering."""

from fastapi import Request
from fastapi.responses import JSONResponse

PROBLEM_JSON_MEDIA_TYPE = "application/problem+json"
ERROR_TYPE_BASE_URL = "https://ishaara.app/errors"


class IshaaraError(Exception):
    """Base class for errors that map to an HTTP problem response.

    Args:
        code: Stable machine-readable error code, e.g. ``video_too_long``.
        status: HTTP status code.
        title: Short human-readable summary.
        detail: Explanation specific to this occurrence.
    """

    def __init__(self, code: str, status: int, title: str, detail: str) -> None:
        """Store the problem fields."""
        super().__init__(detail)
        self.code = code
        self.status = status
        self.title = title
        self.detail = detail


async def problem_json_handler(request: Request, error: Exception) -> JSONResponse:
    """Render an ``IshaaraError`` as an RFC 9457 problem details response.

    Args:
        request: The failing request (unused, required by Starlette).
        error: The raised error; must be an ``IshaaraError``.

    Returns:
        A ``JSONResponse`` with media type ``application/problem+json``.

    Raises:
        TypeError: If ``error`` is not an ``IshaaraError``.
    """
    if not isinstance(error, IshaaraError):
        raise TypeError("problem_json_handler only handles IshaaraError")
    return JSONResponse(
        status_code=error.status,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        content={
            "type": f"{ERROR_TYPE_BASE_URL}/{error.code}",
            "title": error.title,
            "status": error.status,
            "detail": error.detail,
            "code": error.code,
        },
    )
