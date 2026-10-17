"""Video endpoints: submit, status, history, cues, WebVTT description track and notes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from ishaara.api.dependencies import (
    get_blob_storage,
    get_current_user,
    get_ready_video,
    get_video_repository,
    get_video_service,
)
from ishaara.clients.blob_storage import BlobStorage
from ishaara.clients.repositories import VideoRepository
from ishaara.errors import VideoNotFoundError
from ishaara.models.api_models import (
    AuthenticatedUser,
    CuesResponse,
    SubmitVideoRequest,
    UserVideosResponse,
    VideoStatusResponse,
)
from ishaara.models.domain import VideoRecord
from ishaara.renderers.notes import render_notes_html, render_notes_markdown
from ishaara.renderers.webvtt import render_description_track
from ishaara.services.notes_service import load_transcript
from ishaara.services.video_service import VideoService, is_valid_video_key

router = APIRouter(prefix="/v1", dependencies=[Depends(get_current_user)])

MAX_HISTORY_LIMIT = 100
READY_VIDEO = Annotated[VideoRecord, Depends(get_ready_video)]


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
    record = await videos.get(video_key) if is_valid_video_key(video_key) else None
    if record is None:
        raise VideoNotFoundError("No video with that key.")
    return VideoStatusResponse.from_record(record)


@router.get("/me/videos")
async def list_my_videos(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    videos: Annotated[VideoRepository, Depends(get_video_repository)],
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_LIMIT)] = 20,
) -> UserVideosResponse:
    """Return the caller's submitted videos, newest first."""
    return UserVideosResponse(videos=await videos.list_for_user(user.uid, limit))


@router.get("/videos/{video_key}/cues")
async def get_cues(video: READY_VIDEO) -> CuesResponse:
    """Return the narrated cues of a finished video, in time order.

    Raises:
        VideoNotFoundError: If no video has that key.
        VideoNotReadyError: If the video has not finished processing.
    """
    return CuesResponse(cues=sorted(video.cues, key=lambda cue: cue.start_seconds))


@router.get("/videos/{video_key}/cues.vtt")
async def get_cues_vtt(video: READY_VIDEO) -> Response:
    """Return the narration as a WebVTT description track (``text/vtt``)."""
    return Response(render_description_track(video), media_type="text/vtt")


@router.get("/videos/{video_key}/notes")
async def get_notes(
    video: READY_VIDEO,
    blob_storage: Annotated[BlobStorage, Depends(get_blob_storage)],
    notes_format: Annotated[Literal["markdown", "html"], Query(alias="format")] = "markdown",
) -> Response:
    """Return accessible lecture notes as Markdown (default) or a full HTML page."""
    transcript = await load_transcript(blob_storage, video)
    if notes_format == "html":
        return Response(render_notes_html(video, transcript), media_type="text/html")
    return Response(render_notes_markdown(video, transcript), media_type="text/markdown")
