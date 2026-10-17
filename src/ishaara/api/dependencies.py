"""FastAPI dependencies: authentication and client wiring."""

import asyncio
from functools import lru_cache
from typing import Annotated

import firebase_admin
from fastapi import Depends, Request
from firebase_admin import auth

from ishaara.clients.blob_storage import BlobStorage
from ishaara.clients.repositories import VideoRepository
from ishaara.container import AppContainer
from ishaara.errors import InvalidTokenError, VideoNotFoundError, VideoNotReadyError
from ishaara.models.api_models import AuthenticatedUser
from ishaara.models.domain import VideoRecord, VideoStatus
from ishaara.services.video_service import VideoService
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


def get_container(request: Request) -> AppContainer:
    """Return the app's container (built in the lifespan)."""
    container: AppContainer = request.app.state.container
    return container


def get_video_service(container: Annotated[AppContainer, Depends(get_container)]) -> VideoService:
    """Return the video service."""
    return container.video_service


def get_video_repository(
    container: Annotated[AppContainer, Depends(get_container)],
) -> VideoRepository:
    """Return the video repository."""
    return container.videos


def get_blob_storage(container: Annotated[AppContainer, Depends(get_container)]) -> BlobStorage:
    """Return the blob storage."""
    return container.blob_storage


async def get_ready_video(
    video_key: str, videos: Annotated[VideoRepository, Depends(get_video_repository)]
) -> VideoRecord:
    """Load a video whose processing has finished.

    Args:
        video_key: The video's key from the URL path.
        videos: Video storage.

    Returns:
        The stored record, with status ``ready``.

    Raises:
        VideoNotFoundError: If no video has that key.
        VideoNotReadyError: If the video is queued, processing or failed.
    """
    record = await videos.get(video_key)
    if record is None:
        raise VideoNotFoundError(f"No video with key {video_key}.")
    if record.status == VideoStatus.READY:
        return record
    if record.status == VideoStatus.FAILED:
        raise VideoNotReadyError(f"Processing failed ({record.error_code}): {record.error_message}")
    if record.status == VideoStatus.QUEUED:
        raise VideoNotReadyError("This video is queued and has not started processing yet.")
    where = f" (stage {record.stage.value}, {record.progress_percent}%)" if record.stage else ""
    raise VideoNotReadyError(f"This video is still being processed{where}. Try again shortly.")
