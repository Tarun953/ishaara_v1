"""Domain models and enums shared across the pipeline (VideoRecord arrives in M3)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class VideoStatus(StrEnum):
    """Lifecycle state of a processed video."""

    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class PipelineStage(StrEnum):
    """Stage of the processing pipeline, reported while a video is processing."""

    RESOLVING = "resolving"
    ANALYZING = "analyzing"
    WRITING = "writing"
    VOICING = "voicing"
    SAVING = "saving"


class NarrationLanguage(StrEnum):
    """Language the narration is spoken in."""

    ENGLISH = "en"
    HINDI = "hi"


class ReferentKind(StrEnum):
    """What a pointing word refers to on screen."""

    EQUATION = "equation"
    DIAGRAM = "diagram"
    GRAPH = "graph"
    CODE = "code"
    TABLE = "table"
    TEXT = "text"
    PHYSICAL_OBJECT = "physical_object"


class TranscriptSegment(BaseModel):
    """One transcribed sentence with absolute video times."""

    start_seconds: float
    end_seconds: float
    text: str


class SpeechCoverage(StrEnum):
    """How much of a visual event a listener gets from the teacher's speech alone."""

    FULLY_SPOKEN = "fully_spoken"
    PARTLY_SPOKEN = "partly_spoken"
    NOT_SPOKEN = "not_spoken"


class TimedVisualEvent(BaseModel):
    """A judged visual event with an absolute time, before events are merged into cues.

    Attributes:
        window_index: Index of the clip window the event came from; used to spot
            duplicates produced by overlapping windows.
    """

    window_index: int
    start_seconds: float
    referent_kind: ReferentKind
    content_description: str
    words_spoken_nearby: str
    speech_coverage: SpeechCoverage
    gap_summary: str
    confidence: float


class NarrationCue(BaseModel):
    """One pause point in the video with its description and audio."""

    cue_id: str
    start_seconds: float
    spoken_phrase: str  # words the teacher said around the event
    referent_kind: ReferentKind
    board_description: str  # full description of what is on screen
    gap_summary: str = ""  # what a listener would miss without seeing it
    narration_text: str = ""  # filled in by the narration stage (M4)
    audio_url: str | None = None
    audio_duration_seconds: float | None = None
    confidence: float


def restart_update(now: datetime, lease_expires_at: datetime) -> dict[str, Any]:
    """Field changes that reset a job to ``queued`` with a fresh lease."""
    return {
        "status": VideoStatus.QUEUED,
        "stage": None,
        "progress_percent": 0,
        "error_code": None,
        "error_message": None,
        "cues": [],
        "lease_expires_at": lease_expires_at,
        "updated_at": now,
    }


def progress_update(stage: PipelineStage, progress_percent: int, now: datetime) -> dict[str, Any]:
    """Field changes that mark a job ``processing`` at a stage."""
    return {
        "status": VideoStatus.PROCESSING,
        "stage": stage,
        "progress_percent": progress_percent,
        "updated_at": now,
    }


def lease_update(lease_expires_at: datetime, now: datetime) -> dict[str, Any]:
    """Field changes that extend the processing lease."""
    return {"lease_expires_at": lease_expires_at, "updated_at": now}


def ready_update(
    cues: list[NarrationCue],
    model_versions: dict[str, str],
    token_usage: dict[str, int],
    transcript_blob_path: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Field changes that mark a job ``ready`` with its result."""
    return {
        "status": VideoStatus.READY,
        "stage": None,
        "progress_percent": 100,
        "cues": cues,
        "model_versions": model_versions,
        "token_usage": token_usage,
        "transcript_blob_path": transcript_blob_path,
        "lease_expires_at": None,
        "updated_at": now,
    }


def failed_update(error_code: str, error_message: str, now: datetime) -> dict[str, Any]:
    """Field changes that mark a job ``failed``."""
    return {
        "status": VideoStatus.FAILED,
        "error_code": error_code,
        "error_message": error_message,
        "lease_expires_at": None,
        "updated_at": now,
    }


class VideoRecord(BaseModel):
    """One processed video, stored under ``video_key`` and used as job id and cache key.

    State changes are pure methods returning a new record, so every storage adapter
    applies exactly the same rules inside its own atomic read-modify-write.
    """

    video_key: str
    youtube_video_id: str
    narration_language: NarrationLanguage
    pipeline_version: int
    title: str
    channel_title: str
    duration_seconds: int
    status: VideoStatus
    stage: PipelineStage | None = None
    progress_percent: int = 0
    error_code: str | None = None
    error_message: str | None = None
    cues: list[NarrationCue] = Field(default_factory=list)
    transcript_blob_path: str | None = None
    lease_expires_at: datetime | None = None
    model_versions: dict[str, str] = Field(default_factory=dict)
    token_usage: dict[str, int] = Field(default_factory=dict)
    created_by_uid: str
    created_at: datetime
    updated_at: datetime

    def is_restartable(self, now: datetime) -> bool:
        """Say whether a new request may restart this job.

        Args:
            now: The current time.

        Returns:
            True if the job failed, or is queued or processing with no live lease.
        """
        if self.status == VideoStatus.FAILED:
            return True
        lease_is_live = self.lease_expires_at is not None and self.lease_expires_at > now
        return self.status in (VideoStatus.QUEUED, VideoStatus.PROCESSING) and not lease_is_live

    def restarted(self, now: datetime, lease_expires_at: datetime) -> "VideoRecord":
        """Return the record reset to ``queued`` with a fresh lease."""
        return self.model_copy(update=restart_update(now, lease_expires_at))

    def with_progress(
        self, stage: PipelineStage, progress_percent: int, now: datetime
    ) -> "VideoRecord":
        """Return the record marked ``processing`` at the given stage and percentage."""
        return self.model_copy(update=progress_update(stage, progress_percent, now))

    def with_lease(self, lease_expires_at: datetime, now: datetime) -> "VideoRecord":
        """Return the record with a new lease expiry."""
        return self.model_copy(update=lease_update(lease_expires_at, now))

    def ready(
        self,
        cues: list[NarrationCue],
        model_versions: dict[str, str],
        token_usage: dict[str, int],
        transcript_blob_path: str | None,
        now: datetime,
    ) -> "VideoRecord":
        """Return the finished record holding the cues."""
        return self.model_copy(
            update=ready_update(cues, model_versions, token_usage, transcript_blob_path, now)
        )

    def failed(self, error_code: str, error_message: str, now: datetime) -> "VideoRecord":
        """Return the record marked failed with the error."""
        return self.model_copy(update=failed_update(error_code, error_message, now))
