"""Request and response bodies and small shared value models for the HTTP API."""

from pydantic import BaseModel


class AuthenticatedUser(BaseModel):
    """The caller identified from a verified Firebase ID token.

    Attributes:
        uid: Firebase user id.
        email: Email address on the token, if any.
    """

    uid: str
    email: str | None = None


class YouTubeVideoMetadata(BaseModel):
    """The subset of YouTube Data API video fields the pipeline needs.

    Attributes:
        title: Video title.
        channel_title: Name of the uploading channel.
        duration_seconds: Video length in seconds.
        privacy_status: ``public``, ``unlisted`` or ``private``.
        embeddable: Whether the owner allows embedding.
        live_broadcast_content: ``none``, ``live`` or ``upcoming``.
    """

    title: str
    channel_title: str
    duration_seconds: int
    privacy_status: str
    embeddable: bool
    live_broadcast_content: str
