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
