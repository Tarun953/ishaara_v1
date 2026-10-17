"""Video endpoints: submit, status and history (cues, WebVTT and notes arrive in M5)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from ishaara.api.dependencies import get_current_user, get_video_repository, get_video_service
from ishaara.clients.repositories import VideoRepository
from ishaara.errors import VideoNotFoundError
from ishaara.models.api_models import (
    AuthenticatedUser,
    SubmitVideoRequest,
    UserVideosResponse,
    VideoStatusResponse,
)
from ishaara.services.video_service import VideoService

router = APIRouter(prefix="/v1", dependencies=[Depends(get_current_user)])

MAX_HISTORY_LIMIT = 100


@router.post("/videos")
async def submit_video(
    body: SubmitVideoRequest,
    response: Response,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    video_service: Annotated[VideoService, Depends(get_video_service)],
) -> VideoStatusResponse:
    """Submit a YouTube video for processing.

    Returns 200 if the result is already ready and 202 if it is queued or processing.
    """
    result = await video_service.submit(user, body.youtube_url, body.narration_language)
    response.status_code = result.http_status
    return result.video


@router.get("/videos/{video_key}")
async def get_video_status(
    video_key: str, videos: Annotated[VideoRepository, Depends(get_video_repository)]
) -> VideoStatusResponse:
    """Return the processing status of one video.

    Raises:
        VideoNotFoundError: If no video has that key.
    """
    record = await videos.get(video_key)
    if record is None:
        raise VideoNotFoundError(f"No video with key {video_key}.")
    return VideoStatusResponse.from_record(record)


@router.get("/me/videos")
async def list_my_videos(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    videos: Annotated[VideoRepository, Depends(get_video_repository)],
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = 20,
) -> UserVideosResponse:
    """Return the caller's submitted videos, newest first."""
    return UserVideosResponse(videos=await videos.list_for_user(user.uid, limit))
