"""Small ASGI middlewares: request body size limit and response security headers."""

import contextlib

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ishaara.errors import problem_response

API_PATH_PREFIX = "/v1/"


class _BodyTooLargeError(Exception):
    """Raised inside the app's receive call when a streamed body passes the limit."""


class RequestSizeLimitMiddleware:
    """Rejects request bodies larger than a limit with 413 problem+json.

    The limit is checked against the ``Content-Length`` header up front and against the
    bytes actually received, so a chunked upload without a length cannot slip through.

    Args:
        app: The wrapped ASGI app.
        max_body_bytes: Largest accepted request body.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        """Store the app and the limit."""
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve the request unless its body is too large."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared_length = dict(scope["headers"]).get(b"content-length")
        if declared_length is not None and not self._declared_size_is_ok(declared_length):
            await self._reject(scope, receive, send)
            return

        received_bytes = 0
        too_large = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received_bytes, too_large
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    too_large = True
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            # FastAPI turns an error raised while reading the body into its own 400 response;
            # once the limit was exceeded, whatever the app answers is replaced by the 413.
            nonlocal rejected
            if too_large:
                if not rejected:
                    rejected = True
                    await self._reject(scope, receive, send)
                return
            await send(message)

        with contextlib.suppress(_BodyTooLargeError):
            await self.app(scope, limited_receive, guarded_send)
        if too_large and not rejected:
            await self._reject(scope, receive, send)

    def _declared_size_is_ok(self, declared_length: bytes) -> bool:
        """Return False if ``Content-Length`` is not a number or exceeds the limit."""
        try:
            return int(declared_length) <= self.max_body_bytes
        except ValueError:
            return False

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send the 413 problem response."""
        response = problem_response(
            "request_too_large",
            413,
            "Request body too large",
            f"The request body must be at most {self.max_body_bytes} bytes.",
        )
        await response(scope, receive, send)


class SecurityHeadersMiddleware:
    """Adds ``nosniff`` everywhere and ``Cache-Control: no-store`` to API responses.

    API responses are specific to the signed-in user (history, questions, notes), so they
    must not be stored by shared caches. A route that sets its own ``Cache-Control`` keeps it.

    Args:
        app: The wrapped ASGI app.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the app."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve the request, adding headers to the response."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api_path = str(scope["path"]).startswith(API_PATH_PREFIX)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                if is_api_path:
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)
