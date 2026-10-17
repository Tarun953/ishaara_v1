"""Request and response bodies and small shared value models for the HTTP API."""

from datetime import datetime

from pydantic import BaseModel, Field

from ishaara.models.domain import NarrationLanguage, PipelineStage, VideoRecord, VideoStatus


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


class VideoSummary(BaseModel):
    """One row of a user's submission history.

    Attributes:
        video_key: Key of the processed video.
        title: Video title.
        submitted_at: When the user submitted it.
    """

    video_key: str
    title: str
    submitted_at: datetime


class VideoStatusResponse(BaseModel):
    """What the frontend polls while a video is processed.

    Attributes:
        video_key: Key of the video, used in every other video route.
        status: Lifecycle state.
        stage: Current pipeline stage while processing.
        progress_percent: 0 to 100.
        title: Video title.
        duration_seconds: Video length.
        error_code: Set when ``status`` is ``failed``.
        error_message: Human-readable failure reason.
    """

    video_key: str
    status: VideoStatus
    stage: PipelineStage | None
    progress_percent: int
    title: str
    duration_seconds: int
    error_code: str | None
    error_message: str | None

    @classmethod
    def from_record(cls, record: VideoRecord) -> "VideoStatusResponse":
        """Build the response from a stored record."""
        return cls(
            video_key=record.video_key,
            status=record.status,
            stage=record.stage,
            progress_percent=record.progress_percent,
            title=record.title,
            duration_seconds=record.duration_seconds,
            error_code=record.error_code,
            error_message=record.error_message,
        )


class SubmitVideoRequest(BaseModel):
    """Body of ``POST /v1/videos``.

    Attributes:
        youtube_url: A YouTube link or bare video id.
        narration_language: Language to narrate in.
    """

    youtube_url: str = Field(min_length=1, max_length=300)
    narration_language: NarrationLanguage


class UserVideosResponse(BaseModel):
    """Body of ``GET /v1/me/videos``."""

    videos: list[VideoSummary]
