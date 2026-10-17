"""Application errors and their RFC 9457 problem+json rendering."""

from http import HTTPStatus

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON_MEDIA_TYPE = "application/problem+json"
ERROR_TYPE_BASE_URL = "https://ishaara.app/errors"

logger = structlog.get_logger()


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


def _problem_response(code: str, status: int, title: str, detail: str) -> JSONResponse:
    """Build an RFC 9457 problem details response."""
    return JSONResponse(
        status_code=status,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        content={
            "type": f"{ERROR_TYPE_BASE_URL}/{code}",
            "title": title,
            "status": status,
            "detail": detail,
            "code": code,
        },
    )


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
    return _problem_response(error.code, error.status, error.title, error.detail)


async def validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Render a request validation failure as a 422 ``invalid_request`` problem."""
    detail = "The request is invalid."
    if isinstance(error, RequestValidationError) and error.errors():
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        detail = f"{location}: {first['msg']}"
    return _problem_response("invalid_request", 422, "Invalid request", detail)


async def http_exception_handler(request: Request, error: Exception) -> JSONResponse:
    """Render routing errors such as 404 and 405 as problem details."""
    if not isinstance(error, StarletteHTTPException):
        raise TypeError("http_exception_handler only handles HTTP exceptions")
    code = {404: "not_found", 405: "method_not_allowed"}.get(error.status_code, "http_error")
    return _problem_response(
        code, error.status_code, HTTPStatus(error.status_code).phrase, str(error.detail)
    )


async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Log an unhandled exception and answer 500 without leaking internals."""
    logger.error("unhandled_error", path=request.url.path, exc_info=error)
    return _problem_response(
        "internal_error", 500, "Internal server error", "Something went wrong. Try again later."
    )


class InvalidYouTubeUrlError(IshaaraError):
    """Raised when a string is not a recognizable YouTube video URL or id."""

    def __init__(self, detail: str) -> None:
        """Build a 400 ``invalid_youtube_url`` error."""
        super().__init__("invalid_youtube_url", 400, "Invalid YouTube URL", detail)


class VideoNotPublicError(IshaaraError):
    """Raised when a video is private or unlisted."""

    def __init__(self, detail: str) -> None:
        """Build a 422 ``video_not_public`` error."""
        super().__init__("video_not_public", 422, "Video is not public", detail)


class VideoNotEmbeddableError(IshaaraError):
    """Raised when the video owner disabled embedding."""

    def __init__(self, detail: str) -> None:
        """Build a 422 ``video_not_embeddable`` error."""
        super().__init__("video_not_embeddable", 422, "Video cannot be embedded", detail)


class VideoIsLiveError(IshaaraError):
    """Raised for live or upcoming broadcasts."""

    def __init__(self, detail: str) -> None:
        """Build a 422 ``video_is_live`` error."""
        super().__init__("video_is_live", 422, "Video is a live broadcast", detail)


class VideoTooLongError(IshaaraError):
    """Raised when a video exceeds the maximum accepted duration."""

    def __init__(self, detail: str) -> None:
        """Build a 422 ``video_too_long`` error."""
        super().__init__("video_too_long", 422, "Video is too long", detail)


class InvalidTokenError(IshaaraError):
    """Raised when the Firebase ID token is missing or invalid."""

    def __init__(self, detail: str) -> None:
        """Build a 401 ``invalid_token`` error."""
        super().__init__("invalid_token", 401, "Invalid or missing token", detail)


class VideoNotFoundError(IshaaraError):
    """Raised when a video does not exist."""

    def __init__(self, detail: str) -> None:
        """Build a 404 ``video_not_found`` error."""
        super().__init__("video_not_found", 404, "Video not found", detail)


class DailyLimitReachedError(IshaaraError):
    """Raised when a user has used up a daily quota."""

    def __init__(self, detail: str) -> None:
        """Build a 429 ``daily_limit_reached`` error."""
        super().__init__("daily_limit_reached", 429, "Daily limit reached", detail)


class GeminiError(IshaaraError):
    """Raised when a Gemini call fails or returns something unusable.

    Args:
        code: ``gemini_error`` or ``gemini_invalid_response``.
        detail: Explanation safe to show to the user.
    """

    def __init__(self, code: str, detail: str) -> None:
        """Build a 502 error with the given code."""
        super().__init__(code, 502, "Video analysis failed", detail)


class StorageError(IshaaraError):
    """Raised when a file cannot be stored in the configured blob storage."""

    def __init__(self, detail: str) -> None:
        """Build a 502 ``storage_error`` error."""
        super().__init__("storage_error", 502, "Storage failed", detail)


class VideoNotReadyError(IshaaraError):
    """Raised when a result is requested before the video has finished processing."""

    def __init__(self, detail: str) -> None:
        """Build a 409 ``video_not_ready`` error."""
        super().__init__("video_not_ready", 409, "Video is not ready", detail)
