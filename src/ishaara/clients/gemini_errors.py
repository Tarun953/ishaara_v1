"""Classifying the errors Gemini calls can raise.

The Interactions API raises its own exception family (``RateLimitError``,
``APITimeoutError`` and so on), which is *not* a subclass of ``google.genai.errors.APIError``.
These helpers recognise it by its class names and attributes, so they do not depend on a private
SDK module, and they also cover the older ``APIError`` and plain network errors.
"""

import re
from collections.abc import Mapping

import httpx
from google.genai import errors as genai_errors

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})
SERVER_ERROR_FLOOR = 500
RETRY_HINT_CAP_SECONDS = 70.0
_RETRY_IN_PATTERN = re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.IGNORECASE)
_SDK_BASE_NAMES = {"APIStatusError", "APIConnectionError", "APITimeoutError", "RateLimitError"}


def _class_names(error: BaseException) -> set[str]:
    """Return the names of the error's class and all its base classes."""
    return {cls.__name__ for cls in type(error).__mro__}


def sdk_status_code(error: BaseException) -> int | None:
    """Return the HTTP status of an Interactions API error, or ``None`` if it has none."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and "APIStatusError" in _class_names(error):
        return status
    return None


def is_sdk_timeout(error: BaseException) -> bool:
    """Say whether the error is the SDK's own client-side timeout."""
    return "APITimeoutError" in _class_names(error)


def is_sdk_connection_error(error: BaseException) -> bool:
    """Say whether the error is the SDK's connection error (including its timeout)."""
    return "APIConnectionError" in _class_names(error)


def is_sdk_error(error: BaseException) -> bool:
    """Say whether the error comes from the Interactions SDK."""
    return bool(_class_names(error) & _SDK_BASE_NAMES)


def is_retryable(error: BaseException) -> bool:
    """Return True for rate limits, server errors, timeouts and network failures."""
    status = sdk_status_code(error)
    if status is not None:
        return status in RETRYABLE_STATUS_CODES or status >= SERVER_ERROR_FLOOR
    if is_sdk_connection_error(error):
        return True
    if isinstance(error, genai_errors.APIError):
        return error.code in RETRYABLE_STATUS_CODES or error.code >= SERVER_ERROR_FLOOR
    return isinstance(error, httpx.TransportError | TimeoutError)


def retry_delay_hint_seconds(error: BaseException) -> float | None:
    """Return how long Gemini asked us to wait, if it said, capped at 70 seconds.

    Reads the ``Retry-After`` header, or the "retry in 59s" phrase in the error message.
    """
    response = getattr(error, "response", None)
    headers: Mapping[str, str] = getattr(response, "headers", {}) or {}
    header_value = headers.get("retry-after")
    if header_value:
        try:
            return min(float(header_value), RETRY_HINT_CAP_SECONDS)
        except ValueError:
            pass
    match = _RETRY_IN_PATTERN.search(str(error))
    if match:
        return min(float(match.group(1)), RETRY_HINT_CAP_SECONDS)
    return None
