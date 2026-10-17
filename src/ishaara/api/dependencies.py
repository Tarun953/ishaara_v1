"""FastAPI dependencies: authentication and client wiring."""

import asyncio
from functools import lru_cache
from typing import Annotated

import firebase_admin
import httpx
from fastapi import Depends, Request
from firebase_admin import auth

from ishaara.clients.youtube_metadata import YouTubeMetadataClient
from ishaara.errors import InvalidTokenError
from ishaara.models.api_models import AuthenticatedUser
from ishaara.settings import Settings, get_settings

BEARER_PREFIX = "bearer "


@lru_cache
def _ensure_firebase_app() -> firebase_admin.App:
    """Initialize the Firebase Admin app once, using Application Default Credentials."""
    return firebase_admin.initialize_app()


def _extract_bearer_token(request: Request) -> str:
    """Return the token from the ``Authorization`` header.

    Raises:
        InvalidTokenError: If the header is missing or not a Bearer token.
    """
    authorization_header = request.headers.get("Authorization", "")
    if not authorization_header.lower().startswith(BEARER_PREFIX):
        raise InvalidTokenError("Send an 'Authorization: Bearer <Firebase ID token>' header.")
    return authorization_header[len(BEARER_PREFIX) :].strip()


async def get_current_user(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> AuthenticatedUser:
    """Identify the caller from a Firebase ID token.

    Args:
        request: The incoming request.
        settings: Application settings; ``auth_bypass_uid`` skips verification (local only).

    Returns:
        The authenticated user.

    Raises:
        InvalidTokenError: If the token is missing, malformed, expired or rejected.
    """
    if settings.auth_bypass_uid:
        return AuthenticatedUser(uid=settings.auth_bypass_uid)
    firebase_id_token = _extract_bearer_token(request)
    try:
        _ensure_firebase_app()
        decoded_token = await asyncio.to_thread(auth.verify_id_token, firebase_id_token)
    except Exception as error:
        raise InvalidTokenError("The Firebase ID token is invalid or expired.") from error
    return AuthenticatedUser(uid=decoded_token["uid"], email=decoded_token.get("email"))


def get_youtube_client(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> YouTubeMetadataClient:
    """Build a YouTube metadata client on the app's shared HTTP client.

    Args:
        request: The incoming request; ``app.state.http_client`` holds the shared client.
        settings: Application settings providing the YouTube API key.

    Returns:
        A ready-to-use ``YouTubeMetadataClient``.
    """
    http_client: httpx.AsyncClient = request.app.state.http_client
    return YouTubeMetadataClient(settings.youtube_api_key, http_client)
