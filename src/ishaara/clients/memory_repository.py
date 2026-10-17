"""In-memory repositories for tests and database-free local development."""

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from ishaara.clients.repositories import DailyUsageField
from ishaara.clock import Clock, utc_now
from ishaara.models.api_models import VideoSummary
from ishaara.models.domain import NarrationCue, PipelineStage, VideoRecord


class InMemoryVideoRepository:
    """``VideoRepository`` that keeps everything in process memory.

    Args:
        clock: Source of the current time.
    """

    def __init__(self, clock: Clock = utc_now) -> None:
        """Create an empty store."""
        self._clock = clock
        self._records: dict[str, VideoRecord] = {}
        self._submissions: dict[str, dict[str, VideoSummary]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def get(self, video_key: str) -> VideoRecord | None:
        """Return a copy of the record, or ``None``."""
        record = self._records.get(video_key)
        return record.model_copy(deep=True) if record else None

    async def create_if_absent(self, record: VideoRecord) -> tuple[VideoRecord, bool]:
        """Store the record unless the key exists."""
        async with self._lock:
            existing = self._records.get(record.video_key)
            if existing:
                return existing.model_copy(deep=True), False
            self._records[record.video_key] = record.model_copy(deep=True)
            return record.model_copy(deep=True), True

    async def claim_restart(self, video_key: str, lease_expires_at: datetime) -> VideoRecord | None:
        """Reset a restartable record to ``queued``."""
        async with self._lock:
            now = self._clock()
            record = self._records.get(video_key)
            if record is None or not record.is_restartable(now):
                return None
            self._records[video_key] = record.restarted(now, lease_expires_at)
            return self._records[video_key].model_copy(deep=True)

    async def _mutate(self, video_key: str, change: Callable[[VideoRecord], VideoRecord]) -> None:
        """Apply ``change`` to a record under the lock; missing keys are ignored."""
        async with self._lock:
            record = self._records.get(video_key)
            if record is not None:
                self._records[video_key] = change(record)

    async def update_progress(
        self, video_key: str, stage: PipelineStage, progress_percent: int
    ) -> None:
        """Record progress."""
        await self._mutate(
            video_key, lambda record: record.with_progress(stage, progress_percent, self._clock())
        )

    async def renew_lease(self, video_key: str, lease_expires_at: datetime) -> None:
        """Extend the lease."""
        await self._mutate(
            video_key, lambda record: record.with_lease(lease_expires_at, self._clock())
        )

    async def mark_ready(
        self,
        video_key: str,
        cues: list[NarrationCue],
        model_versions: dict[str, str],
        token_usage: dict[str, int],
        transcript_blob_path: str | None,
    ) -> None:
        """Store the result."""
        await self._mutate(
            video_key,
            lambda record: record.ready(
                cues, model_versions, token_usage, transcript_blob_path, self._clock()
            ),
        )

    async def mark_failed(self, video_key: str, error_code: str, error_message: str) -> None:
        """Store the failure."""
        await self._mutate(
            video_key, lambda record: record.failed(error_code, error_message, self._clock())
        )

    async def record_submission(
        self, uid: str, video_key: str, title: str, submitted_at: datetime
    ) -> None:
        """Add or refresh a history row."""
        async with self._lock:
            self._submissions[uid][video_key] = VideoSummary(
                video_key=video_key, title=title, submitted_at=submitted_at
            )

    async def list_for_user(self, uid: str, limit: int) -> list[VideoSummary]:
        """Return the user's history, newest first."""
        rows = sorted(
            self._submissions[uid].values(), key=lambda row: row.submitted_at, reverse=True
        )
        return rows[:limit]


class InMemoryUsageRepository:
    """``UsageRepository`` that keeps counters in process memory.

    Args:
        clock: Source of the current time; the UTC date selects the counter.
    """

    def __init__(self, clock: Clock = utc_now) -> None:
        """Create empty counters."""
        self._clock = clock
        self._counts: dict[tuple[str, str, str], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def increment_and_check(self, uid: str, field: DailyUsageField, limit: int) -> bool:
        """Count one use unless today's limit is reached."""
        counter_key = (uid, self._clock().date().isoformat(), field.value)
        async with self._lock:
            if self._counts[counter_key] >= limit:
                return False
            self._counts[counter_key] += 1
            return True
