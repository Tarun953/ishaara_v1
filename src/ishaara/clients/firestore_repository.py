"""Firestore implementations of the storage ports.

Layout: ``videos/{video_key}``, ``users/{uid}/submissions/{video_key}`` and
``users/{uid}/daily_usage/{YYYY-MM-DD}``. An optional collection prefix (used by tests that run
against a real database) turns ``videos`` into ``<prefix>videos`` and ``users`` into
``<prefix>users``. Only operations that must be atomic use
transactions; single-writer updates are plain partial writes to save quota.
"""

from datetime import datetime
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud import firestore

from ishaara.clients.repositories import DailyUsageField
from ishaara.clients.serialization import to_jsonable
from ishaara.clock import Clock, utc_now
from ishaara.models.api_models import VideoSummary
from ishaara.models.domain import (
    NarrationCue,
    PipelineStage,
    VideoRecord,
    failed_update,
    lease_update,
    progress_update,
    ready_update,
    restart_update,
)

VIDEOS_COLLECTION = "videos"
USERS_COLLECTION = "users"


class FirestoreVideoRepository:
    """``VideoRepository`` backed by Firestore.

    Args:
        client: An async Firestore client.
        clock: Source of the current time.
        collection_prefix: Prepended to the collection names; empty in production.
    """

    def __init__(
        self, client: firestore.AsyncClient, clock: Clock = utc_now, collection_prefix: str = ""
    ) -> None:
        """Store the client, clock and collection names."""
        self._client = client
        self._clock = clock
        self._videos_collection = f"{collection_prefix}{VIDEOS_COLLECTION}"
        self._users_collection = f"{collection_prefix}{USERS_COLLECTION}"

    def _video_ref(self, video_key: str) -> firestore.AsyncDocumentReference:
        return self._client.collection(self._videos_collection).document(video_key)

    def _submissions(self, uid: str) -> firestore.AsyncCollectionReference:
        submissions: firestore.AsyncCollectionReference = (
            self._client.collection(self._users_collection).document(uid).collection("submissions")
        )
        return submissions

    async def get(self, video_key: str) -> VideoRecord | None:
        """Return the record, or ``None``."""
        snapshot = await self._video_ref(video_key).get()
        return VideoRecord.model_validate(snapshot.to_dict()) if snapshot.exists else None

    async def create_if_absent(self, record: VideoRecord) -> tuple[VideoRecord, bool]:
        """Create the record in a transaction unless the key exists."""
        video_ref = self._video_ref(record.video_key)

        @firestore.async_transactional
        async def create(transaction: Any, ref: Any) -> tuple[VideoRecord, bool]:
            snapshot = await ref.get(transaction=transaction)
            if snapshot.exists:
                return VideoRecord.model_validate(snapshot.to_dict()), False
            transaction.create(ref, record.model_dump(mode="json"))
            return record, True

        result: tuple[VideoRecord, bool] = await create(self._client.transaction(), video_ref)
        return result

    async def claim_restart(self, video_key: str, lease_expires_at: datetime) -> VideoRecord | None:
        """Reset a restartable record to ``queued`` in a transaction."""
        video_ref = self._video_ref(video_key)
        now = self._clock()

        @firestore.async_transactional
        async def claim(transaction: Any, ref: Any) -> VideoRecord | None:
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            record = VideoRecord.model_validate(snapshot.to_dict())
            if not record.is_restartable(now):
                return None
            transaction.update(ref, to_jsonable(restart_update(now, lease_expires_at)))
            return record.restarted(now, lease_expires_at)

        result: VideoRecord | None = await claim(self._client.transaction(), video_ref)
        return result

    async def _update_fields(self, video_key: str, fields: dict[str, Any]) -> None:
        """Write a partial update without reading first; a missing record is ignored."""
        try:
            await self._video_ref(video_key).update(to_jsonable(fields))
        except NotFound:
            return

    async def update_progress(
        self, video_key: str, stage: PipelineStage, progress_percent: int
    ) -> None:
        """Record progress."""
        await self._update_fields(
            video_key, progress_update(stage, progress_percent, self._clock())
        )

    async def renew_lease(self, video_key: str, lease_expires_at: datetime) -> None:
        """Extend the lease."""
        await self._update_fields(video_key, lease_update(lease_expires_at, self._clock()))

    async def mark_ready(
        self,
        video_key: str,
        cues: list[NarrationCue],
        model_versions: dict[str, str],
        token_usage: dict[str, int],
        transcript_blob_path: str | None,
    ) -> None:
        """Store the result."""
        await self._update_fields(
            video_key,
            ready_update(cues, model_versions, token_usage, transcript_blob_path, self._clock()),
        )

    async def mark_failed(self, video_key: str, error_code: str, error_message: str) -> None:
        """Store the failure."""
        await self._update_fields(
            video_key, failed_update(error_code, error_message, self._clock())
        )

    async def record_submission(
        self, uid: str, video_key: str, title: str, submitted_at: datetime
    ) -> None:
        """Add or refresh a history row."""
        await (
            self._submissions(uid)
            .document(video_key)
            .set({"video_key": video_key, "title": title, "submitted_at": submitted_at})
        )

    async def list_for_user(self, uid: str, limit: int) -> list[VideoSummary]:
        """Return the user's history, newest first."""
        query = (
            self._submissions(uid)
            .order_by("submitted_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [
            VideoSummary.model_validate(snapshot.to_dict()) async for snapshot in query.stream()
        ]


class FirestoreUsageRepository:
    """``UsageRepository`` backed by Firestore transactions.

    Args:
        client: An async Firestore client.
        clock: Source of the current time; the UTC date selects the counter document.
        collection_prefix: Prepended to the collection name; empty in production.
    """

    def __init__(
        self, client: firestore.AsyncClient, clock: Clock = utc_now, collection_prefix: str = ""
    ) -> None:
        """Store the client, clock and collection name."""
        self._client = client
        self._clock = clock
        self._users_collection = f"{collection_prefix}{USERS_COLLECTION}"

    async def increment_and_check(self, uid: str, field: DailyUsageField, limit: int) -> bool:
        """Count one use unless today's limit is reached."""
        day = self._clock().date().isoformat()
        usage_ref = (
            self._client.collection(self._users_collection)
            .document(uid)
            .collection("daily_usage")
            .document(day)
        )

        @firestore.async_transactional
        async def count(transaction: Any, ref: Any) -> bool:
            snapshot = await ref.get(transaction=transaction)
            current = (snapshot.to_dict() or {}).get(field.value, 0)
            if current >= limit:
                return False
            transaction.set(ref, {field.value: current + 1}, merge=True)
            return True

        counted: bool = await count(self._client.transaction(), usage_ref)
        return counted
