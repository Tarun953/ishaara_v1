"""Storage ports: what the app needs from a database, independent of which one.

Implementations: in-memory (tests, local), Firestore, and Supabase (Postgres).
"""

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ishaara.models.api_models import VideoSummary
from ishaara.models.domain import NarrationCue, PipelineStage, VideoRecord


class DailyUsageField(StrEnum):
    """Counters kept per user per day."""

    VIDEOS_SUBMITTED = "videos_submitted"
    QUESTIONS_ASKED = "questions_asked"


class VideoRepository(Protocol):
    """Persistence for processed videos and per-user submission history."""

    async def get(self, video_key: str) -> VideoRecord | None:
        """Return the record, or ``None`` if it does not exist."""
        ...

    async def create_if_absent(self, record: VideoRecord) -> tuple[VideoRecord, bool]:
        """Store ``record`` unless the key exists, atomically.

        Returns:
            The stored record and ``True`` if it was created, or the existing record and
            ``False`` if the key was already taken.
        """
        ...

    async def claim_restart(self, video_key: str, lease_expires_at: datetime) -> VideoRecord | None:
        """Atomically reset a restartable job to ``queued`` with a new lease.

        Returns:
            The reset record, or ``None`` if the record is missing or not restartable
            (see ``VideoRecord.is_restartable``).
        """
        ...

    async def update_progress(
        self, video_key: str, stage: PipelineStage, progress_percent: int
    ) -> None:
        """Mark the video ``processing`` at ``stage`` with the given percentage."""
        ...

    async def renew_lease(self, video_key: str, lease_expires_at: datetime) -> None:
        """Extend the processing lease (the job's heartbeat)."""
        ...

    async def mark_ready(
        self,
        video_key: str,
        cues: list[NarrationCue],
        model_versions: dict[str, str],
        token_usage: dict[str, int],
        transcript_blob_path: str | None,
    ) -> None:
        """Store the finished result and mark the video ``ready``."""
        ...

    async def mark_failed(self, video_key: str, error_code: str, error_message: str) -> None:
        """Mark the video ``failed`` with the error."""
        ...

    async def record_submission(
        self, uid: str, video_key: str, title: str, submitted_at: datetime
    ) -> None:
        """Add (or refresh) the video in the user's history."""
        ...

    async def list_for_user(self, uid: str, limit: int) -> list[VideoSummary]:
        """Return the user's submissions, newest first."""
        ...


class UsageRepository(Protocol):
    """Per-user daily quota counters."""

    async def increment_and_check(self, uid: str, field: DailyUsageField, limit: int) -> bool:
        """Atomically count one use for today (UTC), unless the limit is already reached.

        Returns:
            ``True`` if the use was counted, ``False`` if ``limit`` uses were already made
            today (the counter is left unchanged).
        """
        ...
